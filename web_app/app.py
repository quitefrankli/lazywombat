import subprocess
import time
from datetime import timedelta
from pathlib import Path
from flask import Flask, Request
from flask_bootstrap import Bootstrap5
from flask_wtf.csrf import CSRFProtect
from werkzeug.middleware.proxy_fix import ProxyFix

from web_app.config import ConfigManager


class SetCookieNoStoreMiddleware:
    """Prevent any cookie-setting response from entering browser caches."""

    def __init__(self, wrapped_app):
        self.wrapped_app = wrapped_app

    def __call__(self, environ, start_response):
        def no_store_start_response(status, headers, exc_info=None):
            if any(name.lower() == "set-cookie" for name, _ in headers):
                headers = [
                    (name, value)
                    for name, value in headers
                    if name.lower() != "cache-control"
                ]
                headers.append(("Cache-Control", "private, no-store"))
            return start_response(status, headers, exc_info)

        return self.wrapped_app(environ, no_store_start_response)


class NabicatRequest(Request):
    """Apply Loft's upload cap before CSRF parses multipart forms."""

    @property
    def max_content_length(self) -> int | None:
        configured_limit = super().max_content_length
        loft = ConfigManager().loft
        if (
            self.method == "POST"
            and self.path.startswith(loft.request_path_prefix)
        ):
            if configured_limit is None:
                return loft.gallery_request_max_bytes
            return min(
                configured_limit,
                loft.gallery_request_max_bytes,
            )
        return configured_limit

    @max_content_length.setter
    def max_content_length(self, value: int | None) -> None:
        self._max_content_length = value


app = Flask(__name__)
app.request_class = NabicatRequest
app.wsgi_app = SetCookieNoStoreMiddleware(
    ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
)


def _load_build_version() -> tuple[str, bool]:
    """Load the current commit's newest tag, or its SHA, once at startup."""
    repo_root = Path(__file__).resolve().parent.parent
    try:
        commit_hash = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root,
            stderr=subprocess.DEVNULL,
            timeout=ConfigManager().git_command_timeout_s,
        ).decode().strip()
    except Exception:
        return str(int(time.time())), False
    if not commit_hash:
        return str(int(time.time())), False

    try:
        tags = subprocess.check_output(
            [
                "git",
                "for-each-ref",
                "--points-at",
                "HEAD",
                "--sort=-creatordate",
                "--format=%(refname:short)",
                "refs/tags",
            ],
            cwd=repo_root,
            stderr=subprocess.DEVNULL,
            timeout=ConfigManager().git_command_timeout_s,
        ).decode().splitlines()
    except Exception:
        tags = []

    newest_tag = next((tag.strip() for tag in tags if tag.strip()), None)
    return (newest_tag, True) if newest_tag else (commit_hash, False)


BUILD_VERSION, BUILD_VERSION_IS_TAG = _load_build_version()
STATIC_VERSION = BUILD_VERSION


@app.url_defaults
def _add_static_version(endpoint, values):
    if endpoint and endpoint.endswith('static') and 'v' not in values:
        registry = app.extensions.get("nabicat_apps")
        app_version = (
            registry.static_version(endpoint)
            if registry is not None
            else None
        )
        values['v'] = app_version or STATIC_VERSION

# Session configuration for longer-lasting sessions
# 30 days session lifetime - especially helpful for mobile/iOS users
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=14)

# Cookie settings for better mobile browser compatibility
app.config['SESSION_COOKIE_SECURE'] = True  # Only send over HTTPS
app.config['SESSION_COOKIE_HTTPONLY'] = True  # Prevent XSS access to session cookie
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'  # CSRF protection while allowing normal navigation

bootstrap = Bootstrap5(app)
csrf = CSRFProtect(app)
