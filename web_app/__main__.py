import re
import json
import click
import logging
import smtplib
import tempfile
import flask_login
import http.cookiejar
import urllib.request

from git import Repo
from packaging.version import Version
from typing import * # type: ignore
from pathlib import Path
from email.mime.text import MIMEText
from flask import Response, abort, render_template, request, send_from_directory, url_for
from flask_apscheduler import APScheduler
from logging.handlers import RotatingFileHandler
from xml.sax.saxutils import escape as xml_escape

from web_app.config import ConfigManager
from web_app.data_interface import DataInterface
from web_app.helpers import get_ip, get_all_data_interfaces, register_all_blueprints
from web_app.redis_client import run_once, ensure_local_redis
from web_app.hammock.data_interface import DataInterface as HammockDataInterface
from web_app.tubio.audio_downloader import AudioDownloader
from web_app.app import app


register_all_blueprints(app)

@app.context_processor
def inject_app_name():
    config = ConfigManager()
    return dict(
        app_name="NabiCat",
        cache_browser_max_size_bytes=config.cache_browser_max_size_bytes,
        cache_service_worker_ready_timeout_ms=config.cache_service_worker_ready_timeout_ms,
        cache_service_worker_message_timeout_ms=config.cache_service_worker_message_timeout_ms,
    )

log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

scheduler = APScheduler()


@scheduler.task('cron', id='scheduled_backup', day_of_week='sun', hour=0, minute=0, misfire_grace_time=3600)
@run_once('scheduled_backup')
def scheduled_backup():
    logging.info("Running scheduled backup")
    backup_dir = DataInterface().generate_backup_dir()
    DataInterface().backup_data(backup_dir)
    for data_interface_class in get_all_data_interfaces():
        data_interface_class().backup_data(backup_dir)
    logging.info("Backup complete")


@scheduler.task('cron', id='scheduled_cookie_keepalive', day='*', hour=4, minute=0, misfire_grace_time=3600)
@run_once('scheduled_cookie_keepalive')
def run_cookie_keepalive() -> None:
    logging.info("Running scheduled cookie keepalive")
    cookie_path = ConfigManager().tubio.cookie_path

    jar = http.cookiejar.MozillaCookieJar(cookie_path)
    jar.load(ignore_discard=True, ignore_expires=True)

    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    opener.addheaders = [
        ("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"),
    ]

    try:
        response = opener.open("https://www.youtube.com/feed/subscriptions", timeout=30)
        jar.save(ignore_discard=True, ignore_expires=True)
        logging.info(f"Cookie keepalive OK - status {response.status}")
    except Exception:
        logging.exception("Cookie keepalive failed")


def send_alert_email(subject: str, body: str) -> None:
    config = ConfigManager()
    msg = MIMEText(body)
    msg['Subject'] = subject
    msg['From'] = config.smtp_user
    msg['To'] = config.alert_email_to
    with smtplib.SMTP(config.smtp_host, config.smtp_port) as server:
        server.starttls()
        server.login(config.smtp_user, config.smtp_password)
        server.sendmail(config.smtp_user, config.alert_email_to, msg.as_string())


def _check_and_update_ytdlp() -> None:
    req_path = Path(__file__).resolve().parents[1] / "requirements.txt"
    req_text = req_path.read_text()

    match = re.search(r'yt-dlp\[default\]>=([\d.]+)', req_text)
    if not match:
        return
    current_ver = match.group(1)

    resp = urllib.request.urlopen("https://pypi.org/pypi/yt-dlp/json")
    latest_ver = json.loads(resp.read())["info"]["version"]

    if Version(latest_ver) <= Version(current_ver):
        logging.info(f"yt-dlp is up to date ({current_ver})")
        return

    logging.info(f"Updating yt-dlp: {current_ver} -> {latest_ver}")
    req_path.write_text(req_text.replace(f"yt-dlp[default]>={current_ver}", f"yt-dlp[default]>={latest_ver}"))

    repo = Repo(req_path.parent)
    repo.index.add(["requirements.txt"])
    repo.index.commit(f"update yt-dlp to {latest_ver}")
    repo.remotes.origin.push()

    from web_app.api import update_server as _update_server
    _update_server()


@scheduler.task('cron', id='scheduled_download_health_check', day='*', hour=4, minute=10, misfire_grace_time=3600)
@run_once('scheduled_download_health_check')
def run_download_health_check() -> None:
    logging.info("Running download health check")

    try:
        _check_and_update_ytdlp()
    except Exception as e:
        logging.exception("yt-dlp update check failed")

    config = ConfigManager()
    video_id = config.tubio.test_video_id

    with tempfile.TemporaryDirectory() as tmp_dir:
        out_path = str(Path(tmp_dir) / "health_check.%(ext)s")
        ydl_opts = AudioDownloader._build_ydl_opts(out_path)

        try:
            AudioDownloader.download_audio_file(video_id, ydl_opts)

            result_file = Path(tmp_dir) / "health_check.m4a"
            if result_file.exists() and result_file.stat().st_size > 0:
                send_alert_email(
                    "Tubio Health Check: OK",
                    f"Download health check passed for video {video_id}.\nFile size: {result_file.stat().st_size} bytes."
                )
                logging.info("Download health check passed")
            else:
                send_alert_email(
                    "Tubio Health Check: FAIL",
                    f"Download completed but output file is missing or empty for video {video_id}."
                )
                logging.error("Download health check failed: output file missing or empty")
        except Exception as e:
            logging.exception("Download health check failed")
            send_alert_email(
                "Tubio Health Check: FAIL",
                f"Download health check failed for video {video_id}.\nError: {e}"
            )


def start_scheduler() -> None:
    scheduler.init_app(app)
    if scheduler.running:
        return
    scheduler.start()


def _skip_request_log(path: str, method: str, config: ConfigManager) -> bool:
    if path in config.request_log_suppressed_paths:
        return True
    if method == 'GET' and path.startswith('/sentinel/api/runs/') and not path.endswith('/rerun'):
        return True
    return False


@app.before_request
def before_request():
    config = ConfigManager()
    if any(request.path.startswith(p) for p in config.known_bot_prefixes) or request.method in config.known_bot_methods:
        abort(404)

    # Auto-login as admin in debug mode
    if ConfigManager().debug_mode and not flask_login.current_user.is_authenticated:
        di = DataInterface()
        with di.edit_users() as users:
            user = users.get("admin")
            if user is None:
                user = di.generate_new_user("admin", "admin")
                user.is_admin = True
                users.add(user)
                logging.info("Debug mode: created admin user")
        flask_login.login_user(user, remember=True)

    if _skip_request_log(request.path, request.method, config):
        return

    message = f"Processing request: client={get_ip()}"
    if flask_login.current_user.is_authenticated:
        message += f", username={flask_login.current_user.id}"
    message += f", path={request.path}, method={request.method}"

    _REDACTED_KEYS = {'password', 'csrf_token', 'cookie', 'secret', 'token'}
    if request.method == 'POST':
        if request.is_json:
            raw = request.get_json(silent=True) or {}
            safe = {k: ('***' if k.lower() in _REDACTED_KEYS else v) for k, v in raw.items()}
            message += f", json={safe}"
        elif request.form:
            safe = {k: ('***' if k.lower() in _REDACTED_KEYS else v) for k, v in request.form.items()}
            message += f", form={safe}"

    if len(message) > 500:
        message = message[:500] + f"... (truncated {len(message) - 500} characters)"
    logging.info(message)

@app.route('/')
def home():
    commit_hash = Repo(".").head.commit.hexsha
    return render_template('home.html', commit_hash=commit_hash)

@app.route('/service-worker.js')
def service_worker():
    """Serve service worker from root for proper scope"""
    return send_from_directory('static', 'service-worker.js', mimetype='application/javascript')

@app.route('/sitemap.xml')
def sitemap():
    cfg = ConfigManager()
    base_url = cfg.site_url.rstrip("/")
    urls = [
        url_for('home'),
        url_for('hammock.index'),
        url_for('crosswords.index'),
        url_for('simulations_api.index'),
        url_for('simulations_api.game_of_life'),
        url_for('simulations_api.astar'),
    ]
    for project in HammockDataInterface().get_posts_by_project():
        for post in project.posts:
            urls.append(url_for('hammock.view_post', project=project.name, post=post))

    locs = "\n".join(
        f"  <url><loc>{xml_escape(base_url + path)}</loc></url>"
        for path in dict.fromkeys(urls)
    )
    return Response(
        f'<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f'{locs}\n'
        f'</urlset>\n',
        mimetype='application/xml',
    )

def configure_logging(debug: bool) -> None:
    config = ConfigManager()
    log_path = Path("logs/web_app.log")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    rotating_log_handler = RotatingFileHandler(str(log_path),
                                                   maxBytes=int(1e6),
                                                   backupCount=config.dev.log_rotation_backup_count)
    logging.basicConfig(level=logging.DEBUG if debug else logging.INFO,
                        handlers=[] if debug else [rotating_log_handler],
                        format='%(asctime)s %(levelname)s %(message)s')
    logging.getLogger("markdown_it").setLevel(logging.INFO)

@click.command()
@click.option('--debug', is_flag=True, default=False)
@click.option('--port', default=80, type=int)
@click.option('--llm-source', type=click.Choice(['meridian', 'codex', 'bedrock']), default=None,
              help='Override the default LLM provider for this process.')
def cli_start(debug: bool, port: int, llm_source: str | None):
    configure_logging(debug=debug)
    cfg = ConfigManager()
    cfg.debug_mode = debug
    if llm_source:
        cfg.llm.api_source = llm_source
    app.secret_key = cfg.flask_secret_key

    ensure_local_redis()

    logging.info("Starting server (llm_source=%s)", cfg.llm.api_source)
    app.run(host='0.0.0.0', port=port, debug=debug)

def prod_entry():
    configure_logging(debug=False)
    app.secret_key = ConfigManager().flask_secret_key
    ConfigManager().debug_mode = False
    app.config["DEPLOY_COMMIT"] = Repo(".").head.commit.hexsha

    logging.info("Starting server")
    if not ConfigManager().deployment_canary:
        start_scheduler()
    return app


if __name__ == '__main__':
    cli_start()
