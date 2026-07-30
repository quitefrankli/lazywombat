import base64
import gzip
import json
import subprocess
import tempfile
import flask
import flask_login
import logging

import requests as http_requests
from io import BytesIO
from pathlib import Path
from flask import request
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from functools import wraps
from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from web_app.app import app
from web_app.config import ConfigManager
from web_app.redis_client import get_redis
from web_app.data_interface import DataInterface
from web_app.users import User
from web_app.errors import *
from web_app.logging_utils import log_event


def get_all_data_interfaces() -> list[DataInterface]:
    from web_app.api.data_interface import DataInterface as APIDataInterface
    from web_app.todoist.data_interface import DataInterface as TodoistDataInterface
    from web_app.metrics.data_interface import DataInterface as MetricsDataInterface
    from web_app.tubio.data_interface import DataInterface as TubioDataInterface
    from web_app.file_store.data_interface import DataInterface as FileStoreDataInterface
    from web_app.hammock.data_interface import DataInterface as HammockDataInterface
    from web_app.sentinel.data_interface import DataInterface as SentinelDataInterface

    return [
        APIDataInterface,
        TodoistDataInterface,
        MetricsDataInterface,
        TubioDataInterface,
        FileStoreDataInterface,
        HammockDataInterface,
        SentinelDataInterface,
    ]


def register_all_blueprints(app):
    from web_app.crosswords import crosswords_api
    from web_app.todoist import todoist_api
    from web_app.tubio import tubio_api
    from web_app.metrics import metrics_api
    from web_app.account_api import account_api
    from web_app.file_store import file_store_api
    from web_app.api import api_api
    from web_app.proxy import proxy_api
    from web_app.hammock import hammock_api
    from web_app.dev import dev_api
    from web_app.simulations import simulations_api
    from web_app.sentinel import sentinel_api
    from web_app.oauth import oauth_api
    from web_app.todoist.api import actions_api

    blueprints = [
        todoist_api,
        crosswords_api,
        tubio_api,
        metrics_api,
        account_api,
        file_store_api,
        api_api,
        proxy_api,
        hammock_api,
        dev_api,
        simulations_api,
        sentinel_api,
        oauth_api,
        actions_api,
    ]

    for blueprint in blueprints:
        app.register_blueprint(blueprint)


_EPHEMERAL_KEY_PREFIX = "nabicat:ephkey:"


def _store_ephemeral_key(session_id: str, private_key) -> None:
    """Persist an ephemeral RSA private key in Redis with a TTL.

    Redis-backed (not in-process) so the handshake and the follow-up encrypted
    request can be served by different gunicorn workers.
    """
    der = private_key.private_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    get_redis().set(
        _EPHEMERAL_KEY_PREFIX + session_id,
        der,
        ex=ConfigManager().ephemeral_key_ttl_s,
    )


def _load_ephemeral_key(session_id: str):
    """Return the stored RSA private key for session_id, or None if absent/expired."""
    der = get_redis().get(_EPHEMERAL_KEY_PREFIX + session_id)
    if not der:
        return None
    return serialization.load_der_private_key(der, password=None)


def _delete_ephemeral_key(session_id: str) -> None:
    get_redis().delete(_EPHEMERAL_KEY_PREFIX + session_id)


login_manager = flask_login.LoginManager()
login_manager.init_app(app)
@login_manager.user_loader
def user_loader(username: str) -> User | None:
    users = DataInterface().load_users()
    return users.get(username, None)

@login_manager.request_loader
def request_loader(request: flask.Request) -> User | None:
    username = request.form.get('username')
    if not username:
        return None
    existing_users = DataInterface().load_users()
    return existing_users.get(username, None)

@login_manager.unauthorized_handler
def unauthorized_handler():
    flask.flash('Log in required', category='error')
    return flask.redirect(flask.url_for('account_api.login', 
                                        next=flask.request.path))

limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=["3 per second"],
    storage_uri=ConfigManager().redis_url,
    strategy="fixed-window", # or "moving-window"
)

def from_req(key: str) -> str:
    val = request.form[key] if key in request.form else request.args[key]
    return val.encode('ascii', 'ignore').decode('ascii')

def admin_only(failure_redirect: str):
    def _admin_only(func):
        @wraps(func)
        def decorated_view(*args, **kwargs):
            if not flask_login.current_user.is_admin:
                flask.flash('You must be an admin to access this page', category='error')
                return flask.redirect(flask.url_for(failure_redirect))

            return func(*args, **kwargs)

        return decorated_view
    return _admin_only

def register_app_name(blueprint, name: str) -> None:
    """Register a context processor exposing ``app_name`` to this blueprint's templates."""
    @blueprint.context_processor
    def _inject_app_name():
        return dict(app_name=name)


def require_login_blueprint(blueprint) -> None:
    """Gate every route in ``blueprint`` behind login."""
    @blueprint.before_request
    @flask_login.login_required
    def _require_login():
        pass


def require_admin_blueprint(blueprint, message: str | None = None, api_prefixes: tuple[str, ...] = ()) -> None:
    """Gate every route in ``blueprint`` behind admin access."""
    @blueprint.before_request
    @flask_login.login_required
    def _require_admin():
        if not flask_login.current_user.is_admin:
            return redirect_with_access_denied(
                message or ConfigManager().admin_access_denied_message,
                api_prefixes,
            )


def redirect_with_access_denied(message: str, api_prefixes: tuple[str, ...] = ()):
    if request.method != 'GET' or any(request.path.startswith(prefix) for prefix in api_prefixes):
        flask.abort(403)
    config = ConfigManager()
    flask.flash(message, category='error')
    return flask.redirect(flask.url_for(config.access_denied_redirect_endpoint))

def get_ip() -> str:
    if request.headers.getlist("X-Forwarded-For"):
        return request.headers.getlist("X-Forwarded-For")[0]
    else:
        return request.remote_addr if request.remote_addr else "Unknown IP"
    
def cur_user() -> User:
    if not isinstance(flask_login.current_user, User):
        raise TypeError("Current user is not an instance of User")
    return flask_login.current_user

def authenticate_user(username: str, password: str, require_admin: bool = True) -> bool:
    if not username or not password:
        return False
    users = DataInterface().load_users()
    user = users.get(username)
    if not user or user.password != password:
        return False

    if require_admin and not user.is_admin:
        return False
    
    return True

def generate_ephemeral_keypair() -> tuple[str, str]:
    """
    Generate an ephemeral RSA key pair for hybrid encryption.
    
    Returns:
        tuple: (session_id, public_key_pem)
        The private key is stored in memory with TTL.
    """
    import uuid
    
    # Generate 2048-bit RSA key pair
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
    )
    
    # Generate unique session ID
    session_id = str(uuid.uuid4())
    
    # Serialize public key to PEM
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo
    ).decode('utf-8')
    
    # Store private key in Redis (TTL from config)
    _store_ephemeral_key(session_id, private_key)
    
    log_event(
        "api", "api.ephemeral_key_generated",
        level=logging.DEBUG, session_id=session_id,
    )
    return session_id, public_pem


def decode_decrypt_decompress(encrypted_payload: dict) -> dict:
    """
    Decrypt hybrid-encrypted payload.
    
    Expected payload format:
    {
        "session_id": "<uuid>",
        "encrypted_key": "<base64>",  # AES key encrypted with RSA public key
        "encrypted_data": "<base64>", # gzip-compressed JSON encrypted with AES-GCM
        "nonce": "<base64>"           # AES-GCM nonce
    }
    """
    try:
        session_id = encrypted_payload['session_id']
        encrypted_key_b64 = encrypted_payload['encrypted_key']
        encrypted_data_b64 = encrypted_payload['encrypted_data']
        nonce_b64 = encrypted_payload['nonce']
    except KeyError as e:
        raise APIError(f"Missing required field in encrypted payload: {e}")
    
    # Retrieve ephemeral private key
    private_key = _load_ephemeral_key(session_id)
    if not private_key:
        raise AuthenticationError("Invalid or expired session ID")
    
    try:
        # Decode base64
        encrypted_key = base64.b64decode(encrypted_key_b64)
        encrypted_data = base64.b64decode(encrypted_data_b64)
        nonce = base64.b64decode(nonce_b64)
        
        # Decrypt AES key using RSA-OAEP
        aes_key = private_key.decrypt(
            encrypted_key,
            padding.OAEP(
                mgf=padding.MGF1(algorithm=hashes.SHA256()),
                algorithm=hashes.SHA256(),
                label=None
            )
        )
        
        # Decrypt data using AES-GCM
        aesgcm = AESGCM(aes_key)
        compressed_data = aesgcm.decrypt(nonce, encrypted_data, None)
        
        # Decompress and parse JSON
        with gzip.GzipFile(fileobj=BytesIO(compressed_data)) as gz:
            json_data = gz.read()
        
        # Clean up: delete the ephemeral key after successful decryption
        _delete_ephemeral_key(session_id)
        
        return json.loads(json_data.decode('utf-8'))
        
    except Exception as e:
        log_event(
            "api", "api.decryption_failed",
            level=logging.WARNING, session_id=session_id, exc_info=e,
            error_type=type(e).__name__,
        )
        raise APIError(f"Failed to decrypt request: {str(e)}")

def parse_request(require_login: bool = True, require_admin: bool = True) -> dict:
    if require_admin:
        require_login = True
    
    content_type = request.headers.get('Content-Type', '')
    if content_type.startswith('application/json'):
        request_body = request.get_json(silent=True)
        if request_body is None:
            raise APIError("Invalid JSON request_body")
    elif content_type.startswith('application/x-www-form-urlencoded'):
        request_body = request.form.to_dict()
    elif content_type.startswith('multipart/form-data'):
        request_body = {key: value for key, value in request.form.items()}
    else:
        raise APIError("Unsupported content type")
    
    # Check if this is a hybrid-encrypted request (new protocol)
    encrypted_payload = request_body.get("req")
    if encrypted_payload:
        if isinstance(encrypted_payload, dict):
            # New hybrid encryption format
            try:
                request_body = decode_decrypt_decompress(encrypted_payload)
            except Exception as e:
                raise APIError(f"Failed to decrypt request: {str(e)}")
        elif isinstance(encrypted_payload, str):
            # Legacy format - deprecated, will be removed
            raise APIError("Legacy encryption format is no longer supported. Please use hybrid encryption.")
    
    if require_login:
        username = request_body.get("username", "")
        password = request_body.get("password", "")
        if not authenticate_user(username, password, require_admin=require_admin):
            raise AuthenticationError("Invalid credentials")

    return request_body


def _build_message_content(user_message: str, image_paths: list | None):
    """Build an Anthropic message content value from text + optional images.

    Returns the plain string when no images are supplied; otherwise a list of
    content blocks (text first, then a base64 image block per existing file).
    Missing files are silently skipped.
    """
    if not image_paths:
        return user_message
    content: list = [{"type": "text", "text": user_message}]
    for path in image_paths:
        p = Path(path)
        if not p.exists():
            continue
        ext = p.suffix.lower()
        media_type = "image/jpeg" if ext in (".jpg", ".jpeg") else "image/png"
        content.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": media_type,
                "data": base64.b64encode(p.read_bytes()).decode("ascii"),
            },
        })
    return content


class MeridianError(RuntimeError):
    """Raised when the Meridian proxy is unreachable or returns an error."""


class CodexCLIError(RuntimeError):
    """Raised when the local Codex CLI is unavailable or returns an error."""


class BedrockError(RuntimeError):
    """Raised when the AWS Bedrock Anthropic call fails."""


_bedrock_clients: dict = {}


def _get_bedrock_client(read_timeout_s: float):
    """Return a cached boto3 bedrock-runtime client with bounded socket timeouts.

    We use boto3 directly rather than the anthropic SDK's AnthropicBedrock
    because the SDK double-URL-encodes inference-profile ARNs in the request
    path, which breaks SigV4 signing. boto3 handles ARN model IDs correctly.

    Region is read from AWS_REGION (or AWS_DEFAULT_REGION); we pass it
    explicitly because profile-level ``region`` in ~/.aws/config takes
    precedence over the AWS_REGION env var otherwise — which would point
    the client at the wrong region for inference-profile ARNs.

    Clients are cached per read-timeout: botocore sets the socket read timeout
    on the client (not per call), so callers with different timeouts get
    distinct clients. Without a read_timeout a stalled connection hangs the
    calling thread forever, which defeats Sentinel's per-step deadline.
    """
    key = round(float(read_timeout_s), 3)
    client = _bedrock_clients.get(key)
    if client is None:
        import os
        import boto3
        from botocore.config import Config

        cfg = ConfigManager().llm
        region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")
        boto_cfg = Config(
            connect_timeout=cfg.bedrock_connect_timeout_s,
            read_timeout=read_timeout_s,
            retries={"max_attempts": cfg.bedrock_max_attempts, "mode": "standard"},
        )
        client = boto3.client("bedrock-runtime", region_name=region, config=boto_cfg)
        _bedrock_clients[key] = client
    return client


def bedrock_text(
    user_message: str,
    system: str,
    model: str,
    max_tokens: int = 1024,
    timeout_s: float = 120.0,
    image_paths: list | None = None,
) -> str:
    """Invoke AWS Bedrock via boto3 with an Anthropic Messages payload.

    Uses the standard AWS credential chain (AWS_REGION + AWS_PROFILE / env
    keys / instance role). ``model`` is a Bedrock foundation-model ID or an
    inference-profile ARN.
    """
    message_content = _build_message_content(user_message, image_paths)

    body = json.dumps({
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": message_content}],
    })

    try:
        resp = _get_bedrock_client(timeout_s).invoke_model(modelId=model, body=body)
    except Exception as e:
        msg = str(e)
        if (
            e.__class__.__name__ in {"ReadTimeoutError", "ConnectTimeoutError", "ConnectionError"}
            or "Read timeout" in msg
            or "Connect timeout" in msg
        ):
            raise BedrockError(f"bedrock call timed out after {timeout_s}s") from e
        if "Unable to locate credentials" in msg or "could not resolve credentials" in msg:
            raise BedrockError(
                "AWS credentials not found. Set AWS_PROFILE in .env (and run aws sso login if needed)."
            ) from e
        if "ExpiredToken" in msg or ("security token" in msg.lower() and "expired" in msg.lower()):
            raise BedrockError("AWS session expired. Refresh your SSO credentials.") from e
        if "AccessDeniedException" in msg or "is not authorized to perform" in msg:
            raise BedrockError(f"Bedrock access denied for model {model!r}: {msg[:300]}") from e
        raise BedrockError(f"bedrock call failed: {e}") from e

    payload = json.loads(resp["body"].read())
    return "".join(
        b.get("text", "") for b in payload.get("content", []) if b.get("type") == "text"
    ).strip()


def call_meridian(
    messages: list,
    system: str,
    model: str | None = None,
    max_tokens: int = 1024,
    tools: list | None = None,
    timeout_s: float = 120.0,
    agent: str = "nabicat",
) -> dict:
    """Send a single /v1/messages request to the local Meridian proxy.

    Returns the parsed JSON response on success. Raises ``MeridianError``
    on network failure or non-2xx response. Does not retry.
    """
    config = ConfigManager()
    headers = {"Content-Type": "application/json", "x-meridian-agent": agent}
    body = {
        "model": model or config.llm.meridian_models.get("medium", ""),
        "max_tokens": max_tokens,
        "system": system,
        "messages": messages,
    }
    if tools is not None:
        body["tools"] = tools

    try:
        resp = http_requests.post(config.llm.meridian_url, headers=headers, json=body, timeout=timeout_s)
    except http_requests.RequestException as e:
        raise MeridianError(f"meridian unreachable: {e}") from e

    if not resp.ok:
        raise MeridianError(f"meridian {resp.status_code}: {resp.text[:500]}")

    try:
        return resp.json()
    except ValueError as e:
        raise MeridianError(f"meridian returned non-JSON: {e}") from e


def meridian_text(
    user_message: str,
    system: str,
    model: str | None = None,
    max_tokens: int = 1024,
    timeout_s: float = 120.0,
    agent: str = "nabicat",
    image_paths: list | None = None,
) -> str:
    """Convenience wrapper around ``call_meridian`` for text/multimodal prompts.

    When ``image_paths`` is provided, each existing PNG/JPEG file is base64-
    encoded and attached as an Anthropic image content block alongside the
    user text. Concatenates all ``text`` blocks from the response and returns
    the stripped result. Raises ``MeridianError`` on transport failures.
    """
    message_content = _build_message_content(user_message, image_paths)

    result = call_meridian(
        messages=[{"role": "user", "content": message_content}],
        system=system,
        model=model,
        max_tokens=max_tokens,
        timeout_s=timeout_s,
        agent=agent,
    )
    blocks = result.get("content", []) or []
    return "".join(b.get("text", "") for b in blocks if b.get("type") == "text").strip()


def codex_cli_text(
    user_message: str,
    instructions: str,
    model: str | None = None,
    timeout_s: float = 120.0,
) -> str:
    """Run Codex CLI non-interactively and return its final message.

    This uses the local Codex installation and its existing ChatGPT/Codex
    authentication, not an OpenAI API key.
    """
    config = ConfigManager()
    prompt = f"{instructions}\n\nUser request:\n{user_message}"
    with tempfile.NamedTemporaryFile("r+", encoding="utf-8") as output:
        cmd = [
            config.llm.codex_cli_command,
            "-a",
            config.llm.codex_cli_approval_policy,
            "exec",
            "--ephemeral",
            "--skip-git-repo-check",
            "--sandbox",
            config.llm.codex_cli_sandbox,
            "--output-last-message",
            output.name,
        ]
        if model:
            cmd.extend(["--model", model])
        cmd.append(prompt)
        try:
            proc = subprocess.run(
                cmd,
                cwd=str(config.project_dir),
                capture_output=True,
                text=True,
                timeout=timeout_s,
                check=False,
            )
        except FileNotFoundError as e:
            raise CodexCLIError(f"codex cli not found: {config.llm.codex_cli_command}") from e
        except subprocess.TimeoutExpired as e:
            raise CodexCLIError(f"codex cli timed out after {timeout_s}s") from e

        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip()
            raise CodexCLIError(f"codex cli exited {proc.returncode}: {detail[:500]}")

        output.seek(0)
        text = output.read().strip()
        if not text:
            raise CodexCLIError("codex cli returned an empty response")
        return text
