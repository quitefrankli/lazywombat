import re
import json
import click
import logging
import smtplib
import tempfile
import time
import uuid
import flask_login
import http.cookiejar
import urllib.request

from git import Repo
from packaging.version import Version
from typing import * # type: ignore
from pathlib import Path
from email.mime.text import MIMEText
from flask import Response, abort, g, redirect, render_template, request, url_for
from flask_apscheduler import APScheduler
from logging.handlers import RotatingFileHandler
from xml.sax.saxutils import escape as xml_escape

from web_app.config import ConfigManager
from web_app.data_interface import DataInterface
from web_app.helpers import get_all_data_interfaces, register_all_blueprints
from web_app.redis_client import run_once, ensure_local_redis
from web_app.logging_utils import log_event
from web_app.loft.data_interface import DataInterface as LoftDataInterface
from web_app.tubio.audio_downloader import AudioDownloader
from web_app.app import BUILD_VERSION, BUILD_VERSION_IS_TAG, app


register_all_blueprints(app)

@app.context_processor
def inject_app_name():
    config = ConfigManager()
    return dict(
        app_name="NabiCat",
        cache_browser_max_size_bytes=config.cache_browser_max_size_bytes,
        cache_service_worker_prefix=config.cache_service_worker_prefix,
        cache_service_worker_ready_timeout_ms=config.cache_service_worker_ready_timeout_ms,
        cache_service_worker_message_timeout_ms=config.cache_service_worker_message_timeout_ms,
    )

log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

scheduler = APScheduler()


@scheduler.task('cron', id='scheduled_backup', day_of_week='sun', hour=0, minute=0, misfire_grace_time=3600)
@run_once('scheduled_backup')
def scheduled_backup():
    log_event("system", "backup.started", source="scheduler")
    backup_dir = DataInterface().generate_backup_dir()
    DataInterface().backup_data(backup_dir)
    for data_interface_class in get_all_data_interfaces():
        data_interface_class().backup_data(backup_dir)
    log_event("system", "backup.completed", source="scheduler")


@scheduler.task('cron', id='scheduled_cookie_keepalive', day='*', hour=4, minute=0, misfire_grace_time=3600)
@run_once('scheduled_cookie_keepalive')
def run_cookie_keepalive() -> None:
    log_event("tubio", "cookie_keepalive.started", source="scheduler")
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
        log_event(
            "tubio", "cookie_keepalive.completed",
            source="scheduler", status=response.status,
        )
    except Exception as error:
        log_event(
            "tubio", "cookie_keepalive.failed",
            level=logging.ERROR, source="scheduler", exc_info=error,
            error_type=type(error).__name__,
        )


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
        log_event("tubio", "ytdlp_update_not_needed", version=current_ver)
        return

    log_event(
        "tubio", "ytdlp_update_started",
        current_version=current_ver, target_version=latest_ver,
    )
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
    log_event("tubio", "download_health_check.started", source="scheduler")

    try:
        _check_and_update_ytdlp()
    except Exception as e:
        log_event(
            "tubio", "ytdlp_update_check.failed",
            level=logging.ERROR, exc_info=e, error_type=type(e).__name__,
        )

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
                log_event("tubio", "download_health_check.completed", result="passed")
            else:
                send_alert_email(
                    "Tubio Health Check: FAIL",
                    f"Download completed but output file is missing or empty for video {video_id}."
                )
                log_event(
                    "tubio", "download_health_check.failed",
                    level=logging.ERROR, reason="missing_or_empty_output",
                )
        except Exception as e:
            log_event(
                "tubio", "download_health_check.failed",
                level=logging.ERROR, exc_info=e, error_type=type(e).__name__,
            )
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


def _request_app() -> str:
    blueprint = request.blueprint or "web"
    return blueprint.split(".", 1)[0].removesuffix("_api")


@app.before_request
def before_request():
    config = ConfigManager()
    g.request_id = uuid.uuid4().hex
    g.request_started_ns = time.monotonic_ns()
    g.request_app = _request_app()
    # Avoid resolving current_user recursively if a low-level event is emitted
    # while Flask-Login's user loader is still running.
    g.request_user = None
    # Scanner paths abort before normal logging and must remain suppressed.
    g.request_log_suppressed = True
    if any(request.path.startswith(p) for p in config.known_bot_prefixes) or request.method in config.known_bot_methods:
        g.invalid_url_redirect_disabled = True
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
                log_event("account", "account.debug_admin_created", user=user)
        flask_login.login_user(user, remember=True)

    if flask_login.current_user.is_authenticated:
        g.request_user = str(flask_login.current_user.id)
    else:
        g.request_user = None

    g.request_log_suppressed = _skip_request_log(request.path, request.method, config)
    if g.request_log_suppressed:
        return

    log_event(
        g.request_app,
        "request.started",
        method=request.method,
        path=request.path,
        route=request.url_rule.rule if request.url_rule else None,
    )


@app.after_request
def after_request(response: Response) -> Response:
    config = ConfigManager()
    request_id = getattr(g, "request_id", None)
    if request_id:
        response.headers[config.request_id_header] = request_id

    endpoint = request.endpoint or ""
    is_versioned_static = (
        endpoint.endswith("static")
        and bool(request.args.get("v"))
    )
    is_public_media = (
        endpoint in config.cache_public_media_endpoints
        and response.cache_control.public
    )
    if (
        response.headers.getlist("Set-Cookie")
        or (
            flask_login.current_user.is_authenticated
            and not is_versioned_static
            and not is_public_media
        )
    ):
        response.headers["Cache-Control"] = "private, no-store"

    if not getattr(g, "request_log_suppressed", True):
        status = response.status_code
        if status >= config.request_log_error_status:
            level = logging.ERROR
        elif status >= config.request_log_warning_status:
            level = logging.WARNING
        else:
            level = logging.INFO
        started_ns = getattr(g, "request_started_ns", time.monotonic_ns())
        log_event(
            getattr(g, "request_app", _request_app()),
            "request.completed",
            level=level,
            method=request.method,
            path=request.path,
            route=request.url_rule.rule if request.url_rule else None,
            status=status,
            duration_ms=round((time.monotonic_ns() - started_ns) / 1_000_000, 3),
        )
    return response


@app.teardown_request
def teardown_request(error: BaseException | None) -> None:
    if error is None or getattr(g, "request_log_suppressed", True):
        return
    log_event(
        getattr(g, "request_app", _request_app()),
        "request.exception",
        level=logging.ERROR,
        exc_info=error,
        method=request.method,
        path=request.path,
        route=request.url_rule.rule if request.url_rule else None,
        error_type=type(error).__name__,
    )

@app.route('/')
def home():
    return render_template(
        'home.html',
        build_version=BUILD_VERSION,
        build_version_is_tag=BUILD_VERSION_IS_TAG,
    )


@app.route(
    "/hammock",
    defaults={"legacy_path": ""},
    methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    strict_slashes=False,
)
@app.route(
    "/hammock/<path:legacy_path>",
    methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
)
def legacy_loft_redirect(legacy_path: str):
    target = url_for("loft.index") + legacy_path
    if request.query_string:
        target += "?" + request.query_string.decode("latin-1")
    return redirect(target, code=308)


@app.errorhandler(404)
def redirect_invalid_url(error):
    if request.url_rule is None and not getattr(
        g, "invalid_url_redirect_disabled", False
    ):
        return redirect(url_for("home"))
    return error


@app.route('/privacy')
def privacy():
    return render_template('privacy.html')


@app.route('/service-worker.js')
def service_worker():
    """Serve service worker from root for proper scope"""
    config = ConfigManager()
    source = (Path(app.static_folder) / 'service-worker.js').read_text()
    source = source.replace(
        '__NABICAT_CACHE_VERSION__',
        json.dumps(config.cache_service_worker_version),
    ).replace(
        '__NABICAT_CACHE_PREFIX__',
        json.dumps(config.cache_service_worker_prefix),
    ).replace(
        '__NABICAT_STATIC_PATH_PREFIXES__',
        json.dumps(config.cache_versioned_static_path_prefixes),
    ).replace(
        '__NABICAT_PUBLIC_MEDIA_PATH_PREFIXES__',
        json.dumps(config.cache_public_media_path_prefixes),
    )
    response = Response(source, mimetype='application/javascript')
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    return response

@app.route('/sitemap.xml')
def sitemap():
    cfg = ConfigManager()
    base_url = cfg.site_url.rstrip("/")
    urls = [
        url_for('home'),
        url_for('privacy'),
        url_for('loft.index'),
        url_for('crosswords.index'),
        url_for('simulations_api.index'),
        url_for('simulations_api.game_of_life'),
        url_for('simulations_api.astar'),
    ]
    for project in LoftDataInterface().get_posts_by_project():
        for post in project.posts:
            urls.append(url_for('loft.view_post', project=project.name, post=post))

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
                        format=config.log_format)
    logging.getLogger("markdown_it").setLevel(logging.INFO)
    logging.getLogger("redis").setLevel(logging.INFO)
    logging.getLogger("apscheduler.scheduler").setLevel(logging.WARNING)

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

    log_event("system", "server.started", llm_source=cfg.llm.api_source, mode="debug")
    # Under --debug, Werkzeug's reloader re-execs this whole module in a child
    # process, so everything from here up runs twice: once in the supervising
    # parent and once in the serving child (WERKZEUG_RUN_MAIN=true). The parent
    # spawns redis-server; the child then finds it already up. That's why cold
    # debug starts log both "Starting local redis-server" and "already running".
    app.run(host='0.0.0.0', port=port, debug=debug)

def prod_entry():
    configure_logging(debug=False)
    app.secret_key = ConfigManager().flask_secret_key
    ConfigManager().debug_mode = False
    app.config["DEPLOY_COMMIT"] = Repo(".").head.commit.hexsha

    log_event("system", "worker.started", mode="production")
    if not ConfigManager().deployment_canary:
        start_scheduler()
    return app


if __name__ == '__main__':
    cli_start()
