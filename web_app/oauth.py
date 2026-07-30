import base64
import binascii
import hashlib
import hmac
import json
import logging
import secrets
from functools import wraps
from urllib.parse import unquote_plus, urlencode

import flask
import flask_login
from flask import Blueprint, request
from werkzeug.security import check_password_hash

from web_app.app import csrf
from web_app.config import ConfigManager
from web_app.data_interface import DataInterface
from web_app.redis_client import get_redis
from web_app.logging_utils import log_event


oauth_api = Blueprint("oauth_api", __name__, url_prefix="/oauth")
_CODE_PREFIX = "nabicat:oauth:code:"
_ACCESS_PREFIX = "nabicat:oauth:access:"
_REFRESH_PREFIX = "nabicat:oauth:refresh:"
_CONSENT_PREFIX = "nabicat:oauth:consent:"
_USER_TOKENS_PREFIX = "nabicat:oauth:user-tokens:"


@oauth_api.before_request
def require_https():
    if not flask.current_app.testing and not ConfigManager().debug_mode and not request.is_secure:
        return _json_error("invalid_request", "OAuth requires HTTPS", 400)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _json_error(error: str, description: str, status: int):
    response = flask.jsonify(error=error, error_description=description)
    response.status_code = status
    response.headers["Cache-Control"] = "no-store"
    return response


def _redirect(uri: str, **params):
    separator = "&" if "?" in uri else "?"
    return flask.redirect(f"{uri}{separator}{urlencode(params)}")


def _valid_authorize_request(values):
    config = ConfigManager()
    client_id = values.get("client_id", "")
    redirect_uri = values.get("redirect_uri", "")
    scope = values.get("scope", "")
    actions = config.gpt_actions
    if not actions.client_id or not (
        actions.client_secret or actions.client_secret_hash
    ):
        return "OAuth is not configured"
    if client_id != actions.client_id:
        return "Unknown client"
    if redirect_uri not in actions.redirect_uris:
        return "Unregistered redirect URI"
    if values.get("response_type") != "code":
        return "Only response_type=code is supported"
    if scope != actions.read_scope:
        return "Unsupported scope"
    return None


def _client_authenticated():
    config = ConfigManager()
    actions = config.gpt_actions
    header = request.headers.get("Authorization", "")
    method = "basic" if header.startswith("Basic ") else "post"
    if not actions.client_id or not (
        actions.client_secret or actions.client_secret_hash
    ):
        log_event(
            "oauth", "oauth.client_authentication_failed",
            level=logging.ERROR, method=method, reason="server_not_configured",
            client_id_configured=bool(actions.client_id),
            secret_configured=bool(actions.client_secret or actions.client_secret_hash),
        )
        return False
    client_id = request.form.get("client_id", "")
    client_secret = request.form.get("client_secret", "")
    if header.startswith("Basic "):
        try:
            decoded = base64.b64decode(header[6:], validate=True).decode()
            client_id, client_secret = decoded.split(":", 1)
            client_id = unquote_plus(client_id)
            client_secret = unquote_plus(client_secret)
        except (binascii.Error, ValueError, UnicodeDecodeError):
            log_event(
                "oauth", "oauth.client_authentication_failed",
                level=logging.WARNING, method="basic",
                reason="malformed_authorization_header",
            )
            return False
    client_id_match = hmac.compare_digest(client_id, actions.client_id)
    if not client_id_match:
        log_event(
            "oauth", "oauth.client_authentication_failed",
            level=logging.WARNING, method=method,
            reason="client_id_mismatch", client_id_match=False,
        )
        return False
    credential_source = "hash" if actions.client_secret_hash else "plaintext"
    if actions.client_secret_hash:
        try:
            authenticated = check_password_hash(
                actions.client_secret_hash, client_secret
            )
        except ValueError:
            log_event(
                "oauth", "oauth.client_authentication_failed",
                level=logging.ERROR, method=method,
                reason="invalid_server_secret_hash", client_id_match=True,
            )
            return False
    else:
        authenticated = hmac.compare_digest(
            client_secret, actions.client_secret
        )
    if not authenticated:
        log_event(
            "oauth", "oauth.client_authentication_failed",
            level=logging.WARNING, method=method, reason="secret_mismatch",
            client_id_match=True, credential_source=credential_source,
        )
        return False
    log_event(
        "oauth", "oauth.client_authenticated",
        method=method, credential_source=credential_source,
    )
    return True


def _store_token(prefix: str, token: str, metadata: dict, ttl: int):
    redis = get_redis()
    key = prefix + _digest(token)
    redis.set(key, json.dumps(metadata), ex=ttl)
    redis.sadd(_USER_TOKENS_PREFIX + metadata["username"], key)


def _issue_tokens(username: str, scope: str):
    config = ConfigManager()
    actions = config.gpt_actions
    access_token = secrets.token_urlsafe(32)
    refresh_token = secrets.token_urlsafe(48)
    metadata = {
        "username": username,
        "client_id": actions.client_id,
        "scope": scope,
    }
    _store_token(
        _ACCESS_PREFIX, access_token, metadata, actions.access_token_ttl_s
    )
    _store_token(
        _REFRESH_PREFIX, refresh_token, metadata, actions.refresh_token_ttl_s
    )
    return {
        "access_token": access_token,
        "token_type": "Bearer",
        "expires_in": actions.access_token_ttl_s,
        "refresh_token": refresh_token,
        "scope": scope,
    }


def _consume(key: str):
    raw = get_redis().getdel(key)
    return json.loads(raw) if raw else None


def revoke_user_tokens(username: str):
    redis = get_redis()
    index_key = _USER_TOKENS_PREFIX + username
    keys = redis.smembers(index_key)
    if keys:
        redis.delete(*keys)
    redis.delete(index_key, _CONSENT_PREFIX + username)


def bearer_user(required_scope: str):
    def decorator(func):
        @wraps(func)
        def wrapped(*args, **kwargs):
            header = request.headers.get("Authorization", "")
            if not header.startswith("Bearer "):
                response = _json_error("invalid_token", "Bearer token required", 401)
                response.headers["WWW-Authenticate"] = "Bearer"
                return response
            raw = get_redis().get(_ACCESS_PREFIX + _digest(header[7:]))
            if not raw:
                response = _json_error(
                    "invalid_token", "Token is invalid or expired", 401
                )
                response.headers["WWW-Authenticate"] = "Bearer"
                return response
            metadata = json.loads(raw)
            if required_scope not in metadata["scope"].split():
                return _json_error("insufficient_scope", "Required scope is missing", 403)
            user = DataInterface().load_users().get(metadata["username"])
            if user is None:
                return _json_error("invalid_token", "Token user no longer exists", 401)
            flask.g.oauth_user = user
            return func(*args, **kwargs)
        return wrapped
    return decorator


@oauth_api.route("/authorize", methods=["GET", "POST"])
def authorize():
    values = request.args if request.method == "GET" else request.form
    error = _valid_authorize_request(values)
    if error:
        log_event(
            "oauth", "oauth.authorization_rejected",
            level=logging.WARNING, reason=error,
        )
        return _json_error("invalid_request", error, 400)

    if not flask_login.current_user.is_authenticated:
        next_url = request.full_path if request.method == "GET" else (
            request.path + "?" + urlencode({
                key: values.get(key, "")
                for key in ("response_type", "client_id", "redirect_uri", "scope", "state")
            })
        )
        return flask.redirect(flask.url_for("account_api.login", next=next_url))

    redirect_uri = values["redirect_uri"]
    state = values.get("state", "")
    if request.method == "GET":
        consent_key = _CONSENT_PREFIX + flask_login.current_user.id
        if get_redis().get(consent_key) == ConfigManager().gpt_actions.read_scope.encode():
            return _create_authorization_redirect(redirect_uri, state)
        return flask.render_template(
            "oauth_authorize.html",
            oauth_request={key: values.get(key, "") for key in (
                "response_type", "client_id", "redirect_uri", "scope", "state"
            )},
        )

    if values.get("decision") != "approve":
        log_event(
            "oauth", "oauth.consent_denied",
            user=flask_login.current_user,
        )
        return _redirect(redirect_uri, error="access_denied", state=state)
    get_redis().set(
        _CONSENT_PREFIX + flask_login.current_user.id,
        ConfigManager().gpt_actions.read_scope,
        ex=ConfigManager().gpt_actions.consent_ttl_s,
    )
    log_event(
        "oauth", "oauth.consent_granted",
        user=flask_login.current_user,
    )
    return _create_authorization_redirect(redirect_uri, state)


def _create_authorization_redirect(redirect_uri: str, state: str):
    code = secrets.token_urlsafe(32)
    metadata = {
        "username": flask_login.current_user.id,
        "client_id": ConfigManager().gpt_actions.client_id,
        "redirect_uri": redirect_uri,
        "scope": ConfigManager().gpt_actions.read_scope,
    }
    get_redis().set(
        _CODE_PREFIX + _digest(code),
        json.dumps(metadata),
        ex=ConfigManager().gpt_actions.authorization_code_ttl_s,
    )
    return _redirect(redirect_uri, code=code, state=state)


@oauth_api.route("/token", methods=["POST"])
def token():
    grant_type = request.form.get("grant_type")
    if not _client_authenticated():
        response = _json_error("invalid_client", "Client authentication failed", 401)
        response.headers["WWW-Authenticate"] = "Basic"
        return response

    if grant_type == "authorization_code":
        code = request.form.get("code", "")
        key = _CODE_PREFIX + _digest(code)
        raw = get_redis().get(key)
        if not raw:
            log_event(
                "oauth", "oauth.token_exchange_rejected",
                level=logging.WARNING, grant_type="authorization_code",
                reason="invalid_or_expired_code",
            )
            return _json_error("invalid_grant", "Code is invalid or expired", 400)
        metadata = json.loads(raw)
        if (
            metadata["client_id"] != ConfigManager().gpt_actions.client_id
            or metadata["redirect_uri"] != request.form.get("redirect_uri")
        ):
            log_event(
                "oauth", "oauth.token_exchange_rejected",
                level=logging.WARNING, grant_type="authorization_code",
                reason="code_binding_mismatch",
            )
            return _json_error("invalid_grant", "Code binding does not match", 400)
        metadata = _consume(key)
    elif grant_type == "refresh_token":
        metadata = _consume(
            _REFRESH_PREFIX + _digest(request.form.get("refresh_token", ""))
        )
        if not metadata or metadata["client_id"] != ConfigManager().gpt_actions.client_id:
            log_event(
                "oauth", "oauth.token_exchange_rejected",
                level=logging.WARNING, grant_type="refresh_token",
                reason="invalid_or_expired_refresh_token",
            )
            return _json_error("invalid_grant", "Refresh token is invalid or expired", 400)
    else:
        log_event(
            "oauth", "oauth.token_exchange_rejected",
            level=logging.WARNING, grant_type=grant_type or "missing",
            reason="unsupported_grant_type",
        )
        return _json_error("unsupported_grant_type", "Unsupported grant type", 400)

    log_event(
        "oauth", "oauth.tokens_issued",
        user=metadata["username"], grant_type=grant_type,
    )
    response = flask.jsonify(_issue_tokens(metadata["username"], metadata["scope"]))
    response.headers["Cache-Control"] = "no-store"
    return response


@oauth_api.route("/revoke", methods=["POST"])
def revoke():
    if not _client_authenticated():
        return _json_error("invalid_client", "Client authentication failed", 401)
    token_value = request.form.get("token", "")
    digest = _digest(token_value)
    redis = get_redis()
    access_key = _ACCESS_PREFIX + digest
    refresh_key = _REFRESH_PREFIX + digest
    raw = redis.get(access_key) or redis.get(refresh_key)
    metadata = json.loads(raw) if raw else None
    redis.delete(access_key, refresh_key)
    log_event(
        "oauth", "oauth.token_revoked",
        user=metadata.get("username") if metadata else None,
        token_found=metadata is not None,
    )
    return "", 200


csrf.exempt(token)
csrf.exempt(revoke)
