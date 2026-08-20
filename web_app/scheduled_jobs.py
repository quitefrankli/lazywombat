"""Standalone scheduled jobs invoked by systemd timer units."""

from __future__ import annotations

import http.cookiejar
import logging
import smtplib
import tempfile
import urllib.request
from email.mime.text import MIMEText
from pathlib import Path

import click

from web_app.app import app
from web_app.config import ConfigManager
from web_app.data_interface import DataInterface
from web_app.helpers import (
    backup_installed_app_data,
    get_all_data_interfaces,
    register_installed_apps,
)
from web_app.logging_utils import configure_logging, log_event
from web_app.redis_client import ensure_local_redis
from web_app.tubio.audio_downloader import AudioDownloader


def run_backup() -> None:
    job_id = ConfigManager().scheduled_backup_job_id
    log_event("system", "backup.started", source="systemd", job_id=job_id)
    try:
        ensure_local_redis()
        register_installed_apps(app)
        data_interface = DataInterface()
        backup_dir = data_interface.generate_backup_dir()
        data_interface.backup_data(backup_dir)
        subapp_interfaces = get_all_data_interfaces()
        for data_interface_class in subapp_interfaces:
            data_interface_class().backup_data(backup_dir)
        backup_installed_app_data(backup_dir)
    except Exception as error:
        log_event(
            "system",
            "backup.failed",
            level=logging.ERROR,
            source="systemd",
            job_id=job_id,
            exc_info=error,
            error_type=type(error).__name__,
        )
        raise
    log_event(
        "system",
        "backup.completed",
        source="systemd",
        job_id=job_id,
        data_interfaces=len(subapp_interfaces) + 1,
    )


def run_cookie_keepalive() -> None:
    job_id = ConfigManager().scheduled_cookie_keepalive_job_id
    log_event(
        "tubio",
        "cookie_keepalive.started",
        source="systemd",
        job_id=job_id,
    )
    try:
        config = ConfigManager().tubio
        jar = http.cookiejar.MozillaCookieJar(config.cookie_path)
        jar.load(ignore_discard=True, ignore_expires=True)

        opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(jar)
        )
        opener.addheaders = [("User-Agent", config.cookie_keepalive_user_agent)]
        with opener.open(
            config.cookie_keepalive_url,
            timeout=config.cookie_keepalive_timeout_s,
        ) as response:
            status = response.status
            jar.save(ignore_discard=True, ignore_expires=True)
    except Exception as error:
        log_event(
            "tubio",
            "cookie_keepalive.failed",
            level=logging.ERROR,
            source="systemd",
            job_id=job_id,
            exc_info=error,
            error_type=type(error).__name__,
        )
        raise
    log_event(
        "tubio",
        "cookie_keepalive.completed",
        source="systemd",
        job_id=job_id,
        status=status,
    )


def send_alert_email(subject: str, body: str) -> None:
    config = ConfigManager()
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = config.smtp_user
    msg["To"] = config.alert_email_to
    with smtplib.SMTP(config.smtp_host, config.smtp_port) as server:
        server.starttls()
        server.login(config.smtp_user, config.smtp_password)
        server.sendmail(
            config.smtp_user,
            config.alert_email_to,
            msg.as_string(),
        )


def run_download_health_check() -> None:
    job_id = ConfigManager().scheduled_download_health_check_job_id
    log_event(
        "tubio",
        "download_health_check.started",
        source="systemd",
        job_id=job_id,
    )
    video_id = None
    try:
        config = ConfigManager()
        video_id = config.tubio.test_video_id
        with tempfile.TemporaryDirectory() as tmp_dir:
            out_path = str(Path(tmp_dir) / "health_check.%(ext)s")
            ydl_opts = AudioDownloader._build_ydl_opts(out_path)
            AudioDownloader.download_audio_file(video_id, ydl_opts)
            result_file = (
                Path(tmp_dir)
                / f"health_check.{config.tubio.youtube_audio_preferred_codec}"
            )
            if not result_file.exists() or result_file.stat().st_size <= 0:
                raise RuntimeError("download output is missing or empty")
            file_size = result_file.stat().st_size
    except Exception as error:
        log_event(
            "tubio",
            "download_health_check.failed",
            level=logging.ERROR,
            source="systemd",
            job_id=job_id,
            exc_info=error,
            error_type=type(error).__name__,
        )
        try:
            send_alert_email(
                "Tubio Health Check: FAIL",
                f"Download health check failed for video {video_id}.\nError: {error}",
            )
        except Exception as alert_error:
            log_event(
                "tubio",
                "download_health_check.alert_failed",
                level=logging.ERROR,
                source="systemd",
                job_id=job_id,
                notification="failure",
                exc_info=alert_error,
                error_type=type(alert_error).__name__,
            )
        raise

    try:
        send_alert_email(
            "Tubio Health Check: OK",
            (
                f"Download health check passed for video {video_id}.\n"
                f"File size: {file_size} bytes."
            ),
        )
    except Exception as alert_error:
        log_event(
            "tubio",
            "download_health_check.alert_failed",
            level=logging.ERROR,
            source="systemd",
            job_id=job_id,
            notification="success",
            exc_info=alert_error,
            error_type=type(alert_error).__name__,
        )
        raise

    log_event(
        "tubio",
        "download_health_check.completed",
        source="systemd",
        job_id=job_id,
        result="passed",
    )


@click.command()
@click.argument(
    "job_name",
    type=click.Choice(
        tuple(spec[1] for spec in ConfigManager().scheduled_job_timers)
    ),
)
def cli(job_name: str) -> None:
    """Run one configured scheduled job."""
    config = ConfigManager()
    config.debug_mode = False
    configure_logging(debug=False)

    jobs = {
        config.scheduled_backup_job_id: run_backup,
        config.scheduled_cookie_keepalive_job_id: run_cookie_keepalive,
        config.scheduled_download_health_check_job_id: run_download_health_check,
    }
    jobs[job_name]()


if __name__ == "__main__":
    cli()
