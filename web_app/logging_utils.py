"""Consistent structured application and audit event logging."""

from __future__ import annotations

import json
import logging
from typing import Any

import flask
import flask_login
from concurrent_log_handler import ConcurrentRotatingFileHandler

from web_app.config import ConfigManager


def configure_logging(debug: bool) -> None:
    """Configure consistent process-safe logging for web and scheduled workers."""
    config = ConfigManager()
    log_path = config.log_file_path.resolve()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    rotating_log_handler = ConcurrentRotatingFileHandler(
        str(log_path),
        maxBytes=config.dev.log_rotation_max_bytes,
        backupCount=config.dev.log_rotation_backup_count,
    )
    logging.basicConfig(
        level=logging.DEBUG if debug else logging.INFO,
        handlers=[] if debug else [rotating_log_handler],
        format=config.log_format,
    )
    logging.getLogger("markdown_it").setLevel(logging.INFO)
    logging.getLogger("redis").setLevel(logging.INFO)


def _user_id(user: Any | None) -> str | None:
    if user is None:
        return None
    if isinstance(user, (str, int)):
        return str(user)
    value = getattr(user, "id", None)
    if value is None and hasattr(user, "get_id"):
        value = user.get_id()
    return str(value) if value is not None else None


def _request_user() -> str | None:
    if not flask.has_request_context():
        return None
    if hasattr(flask.g, "request_user"):
        captured = flask.g.request_user
        return str(captured) if captured is not None else None
    try:
        if flask_login.current_user.is_authenticated:
            return _user_id(flask_login.current_user)
    except (AttributeError, RuntimeError):
        pass
    return None


def _request_ip() -> str | None:
    if not flask.has_request_context():
        return None
    forwarded = flask.request.headers.getlist("X-Forwarded-For")
    if forwarded:
        return forwarded[0]
    return flask.request.remote_addr


def log_event(
    app: str,
    event: str,
    *,
    level: int = logging.INFO,
    user: Any | None = None,
    ip: str | None = None,
    exc_info: bool | BaseException | tuple | None = None,
    **details: Any,
) -> None:
    """Emit one JSON event with a consistent request/audit context."""
    payload: dict[str, Any] = {
        "app": app,
        "event": event,
        "ip": ip if ip is not None else _request_ip(),
        "user": _user_id(user) if user is not None else _request_user(),
    }
    if flask.has_request_context():
        request_id = getattr(flask.g, "request_id", None)
        if request_id:
            payload["request_id"] = request_id
    payload.update(details)

    if isinstance(exc_info, BaseException):
        exc_info = (type(exc_info), exc_info, exc_info.__traceback__)
    logging.getLogger(app).log(
        level,
        json.dumps(payload, sort_keys=True, default=str),
        exc_info=exc_info,
    )
