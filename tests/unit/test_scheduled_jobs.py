import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner


def test_backup_job_dispatches_all_backup_sources():
    from web_app import scheduled_jobs

    backup_dir = Path("/tmp/nabicat-test-backup")
    root_interface = MagicMock()
    root_interface.generate_backup_dir.return_value = backup_dir
    subapp_interface_1 = MagicMock()
    subapp_interface_2 = MagicMock()

    with (
        patch.object(scheduled_jobs, "ensure_local_redis") as ensure_redis,
        patch.object(scheduled_jobs, "register_installed_apps") as register_apps,
        patch.object(scheduled_jobs, "DataInterface", return_value=root_interface),
        patch.object(
            scheduled_jobs,
            "get_all_data_interfaces",
            return_value=[subapp_interface_1, subapp_interface_2],
        ),
        patch.object(scheduled_jobs, "backup_installed_app_data") as backup_apps,
    ):
        scheduled_jobs.run_backup()

    ensure_redis.assert_called_once_with()
    register_apps.assert_called_once_with(scheduled_jobs.app)
    root_interface.generate_backup_dir.assert_called_once_with()
    root_interface.backup_data.assert_called_once_with(backup_dir)
    subapp_interface_1.return_value.backup_data.assert_called_once_with(backup_dir)
    subapp_interface_2.return_value.backup_data.assert_called_once_with(backup_dir)
    backup_apps.assert_called_once_with(backup_dir)


@pytest.mark.parametrize("failure_stage", ["options", "download"])
def test_download_health_failure_alerts_and_fails_the_systemd_job(failure_stage):
    from web_app import scheduled_jobs

    config = SimpleNamespace(
        scheduled_download_health_check_job_id="download-health-check",
        tubio=SimpleNamespace(test_video_id="dQw4w9WgXcQ"),
    )
    download_error = RuntimeError("download failed")

    build_options = patch.object(
        scheduled_jobs.AudioDownloader,
        "_build_ydl_opts",
        side_effect=download_error if failure_stage == "options" else None,
        return_value={} if failure_stage == "download" else None,
    )
    download = patch.object(
        scheduled_jobs.AudioDownloader,
        "download_audio_file",
        side_effect=download_error if failure_stage == "download" else None,
    )

    with (
        patch.object(scheduled_jobs, "ConfigManager", return_value=config),
        build_options,
        download,
        patch.object(scheduled_jobs, "send_alert_email") as send_alert,
        patch.object(scheduled_jobs, "log_event") as log_event,
        pytest.raises(RuntimeError, match="download failed"),
    ):
        scheduled_jobs.run_download_health_check()

    send_alert.assert_called_once()
    assert "FAIL" in send_alert.call_args.args[0]
    log_event.assert_any_call(
        "tubio",
        "download_health_check.failed",
        level=logging.ERROR,
        source="systemd",
        job_id="download-health-check",
        exc_info=download_error,
        error_type="RuntimeError",
    )


def test_download_health_logs_success_alert_failure(tmp_path):
    from web_app import scheduled_jobs

    config = SimpleNamespace(
        scheduled_download_health_check_job_id="download-health-check",
        tubio=SimpleNamespace(
            test_video_id="dQw4w9WgXcQ",
            youtube_audio_preferred_codec="m4a",
        )
    )
    result_file = tmp_path / "health_check.m4a"
    result_file.write_bytes(b"audio")
    temp_dir = MagicMock()
    temp_dir.__enter__.return_value = str(tmp_path)
    alert_error = RuntimeError("smtp down")

    with (
        patch.object(scheduled_jobs, "ConfigManager", return_value=config),
        patch.object(
            scheduled_jobs.tempfile,
            "TemporaryDirectory",
            return_value=temp_dir,
        ),
        patch.object(scheduled_jobs.AudioDownloader, "_build_ydl_opts", return_value={}),
        patch.object(scheduled_jobs.AudioDownloader, "download_audio_file"),
        patch.object(
            scheduled_jobs,
            "send_alert_email",
            side_effect=alert_error,
        ),
        patch.object(scheduled_jobs, "log_event") as log_event,
        pytest.raises(RuntimeError, match="smtp down"),
    ):
        scheduled_jobs.run_download_health_check()

    log_event.assert_any_call(
        "tubio",
        "download_health_check.alert_failed",
        level=logging.ERROR,
        source="systemd",
        job_id="download-health-check",
        notification="success",
        exc_info=alert_error,
        error_type="RuntimeError",
    )


def test_cli_dispatches_the_selected_job_once():
    from web_app import scheduled_jobs

    config = SimpleNamespace(
        debug_mode=True,
        scheduled_backup_job_id="backup",
        scheduled_cookie_keepalive_job_id="cookie-keepalive",
        scheduled_download_health_check_job_id="download-health-check",
    )
    with (
        patch.object(scheduled_jobs, "ConfigManager", return_value=config),
        patch.object(scheduled_jobs, "configure_logging") as configure_logging,
        patch.object(scheduled_jobs, "run_backup") as run_backup,
    ):
        result = CliRunner().invoke(scheduled_jobs.cli, ["backup"])

    assert result.exit_code == 0, result.output
    assert config.debug_mode is False
    configure_logging.assert_called_once_with(debug=False)
    run_backup.assert_called_once_with()
