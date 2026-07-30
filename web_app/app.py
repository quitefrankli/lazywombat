import subprocess
import time
from datetime import timedelta
from pathlib import Path
from flask import Flask, Request, g
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
    """Apply Hammock's upload cap before CSRF parses multipart forms."""

    @property
    def max_content_length(self) -> int | None:
        configured_limit = super().max_content_length
        hammock = ConfigManager().hammock
        if (
            self.method == "POST"
            and self.path.startswith(hammock.request_path_prefix)
        ):
            if configured_limit is None:
                return hammock.gallery_request_max_bytes
            return min(
                configured_limit,
                hammock.gallery_request_max_bytes,
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


def _compute_static_version() -> str:
    try:
        repo_root = Path(__file__).resolve().parent.parent
        sha = subprocess.check_output(
            ['git', 'rev-parse', '--short', 'HEAD'],
            cwd=repo_root, stderr=subprocess.DEVNULL, timeout=2,
        ).decode().strip()
        if sha:
            return sha
    except Exception:
        pass
    return str(int(time.time()))


@app.url_defaults
def _add_static_version(endpoint, values):
    if endpoint and endpoint.endswith('static') and 'v' not in values:
        if not hasattr(g, 'static_version'):
            g.static_version = _compute_static_version()
        values['v'] = g.static_version

# Session configuration for longer-lasting sessions
# 30 days session lifetime - especially helpful for mobile/iOS users
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=14)

# Cookie settings for better mobile browser compatibility
app.config['SESSION_COOKIE_SECURE'] = True  # Only send over HTTPS
app.config['SESSION_COOKIE_HTTPONLY'] = True  # Prevent XSS access to session cookie
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'  # CSRF protection while allowing normal navigation

bootstrap = Bootstrap5(app)
csrf = CSRFProtect(app)
