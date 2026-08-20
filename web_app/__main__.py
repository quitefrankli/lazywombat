import json
import click
import logging
import time
import uuid
import flask_login
from urllib.parse import unquote

from git import Repo
from typing import * # type: ignore
from pathlib import Path
from flask import Response, abort, g, redirect, render_template, request, url_for
from xml.sax.saxutils import escape as xml_escape

from web_app.config import ConfigManager
from web_app.data_interface import DataInterface
from web_app.helpers import (
    register_all_blueprints,
    register_installed_apps,
)
from web_app.redis_client import ensure_local_redis
from web_app.logging_utils import configure_logging, log_event
from web_app.loft.data_interface import DataInterface as LoftDataInterface
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

def _skip_request_log(path: str, method: str, config: ConfigManager) -> bool:
    return path in config.request_log_suppressed_paths


def _has_scanner_path_signature(path: str, config: ConfigManager) -> bool:
    segments = tuple(
        segment
        for segment in unquote(path).casefold().split("/")
        if segment
    )
    return any(
        segment in config.scanner_path_segment_names
        or segment.startswith(config.scanner_path_segment_prefixes)
        for segment in segments
    )


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
    if (
        request.method.upper() in config.scanner_methods
        or (
            request.url_rule is None
            and _has_scanner_path_signature(request.path, config)
        )
    ):
        abort(404)

    # Auto-login as admin in debug mode.
    if config.debug_mode and not flask_login.current_user.is_authenticated:
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
    registry = app.extensions.get("nabicat_apps")
    return render_template(
        'home.html',
        build_version=BUILD_VERSION,
        build_version_is_tag=BUILD_VERSION_IS_TAG,
        installed_apps=registry.navigation() if registry is not None else (),
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


@app.route('/privacy')
def privacy():
    return render_template('privacy.html')


@app.route('/service-worker.js')
def service_worker():
    """Serve service worker from root for proper scope"""
    config = ConfigManager()
    source = (Path(app.static_folder) / 'service-worker.js').read_text()
    registry = app.extensions.get("nabicat_apps")
    static_prefixes = config.cache_versioned_static_path_prefixes + (
        registry.static_prefixes() if registry is not None else ()
    )
    source = source.replace(
        '__NABICAT_CACHE_VERSION__',
        json.dumps(config.cache_service_worker_version),
    ).replace(
        '__NABICAT_CACHE_PREFIX__',
        json.dumps(config.cache_service_worker_prefix),
    ).replace(
        '__NABICAT_STATIC_PATH_PREFIXES__',
        json.dumps(static_prefixes),
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


@app.route('/robots.txt')
def robots_txt():
    config = ConfigManager()
    sitemap_url = f'{config.site_url.rstrip("/")}{url_for("sitemap")}'
    return Response(
        "User-agent: *\n"
        "Allow: /\n"
        f"Sitemap: {sitemap_url}\n",
        mimetype="text/plain",
    )


@click.command()
@click.option('--debug', is_flag=True, default=False)
@click.option('--port', default=ConfigManager().server_default_port, type=int)
@click.option('--llm-source', type=click.Choice(['meridian', 'codex', 'bedrock']), default=None,
              help='Override the default LLM provider for this process.')
def cli_start(
    debug: bool,
    port: int,
    llm_source: str | None,
):
    configure_logging(debug=debug)
    cfg = ConfigManager()
    cfg.debug_mode = debug
    if llm_source:
        cfg.llm.api_source = llm_source
    app.secret_key = cfg.flask_secret_key
    app.config["SESSION_COOKIE_NAME"] = cfg.flask_session_cookie_name

    ensure_local_redis()
    register_installed_apps(app)

    mode = "debug" if debug else "development"
    log_event("system", "server.started", llm_source=cfg.llm.api_source, mode=mode)
    # Under --debug, Werkzeug's reloader re-execs this whole module in a child
    # process, so everything from here up runs twice: once in the supervising
    # parent and once in the serving child (WERKZEUG_RUN_MAIN=true). The parent
    # spawns redis-server; the child then finds it already up. That's why cold
    # debug starts log both "Starting local redis-server" and "already running".
    app.run(
        host=cfg.server_host,
        port=port,
        debug=debug,
    )

def prod_entry():
    configure_logging(debug=False)
    config = ConfigManager()
    config.debug_mode = False
    app.secret_key = config.flask_secret_key
    app.config["SESSION_COOKIE_NAME"] = config.flask_session_cookie_name
    app.config["DEPLOY_COMMIT"] = Repo(".").head.commit.hexsha

    ensure_local_redis()
    register_installed_apps(app)

    log_event("system", "worker.started", mode="production")
    return app


if __name__ == '__main__':
    cli_start()
