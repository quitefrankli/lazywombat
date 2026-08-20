import atexit
from contextlib import contextmanager
import logging
import math
import shutil
import subprocess
import threading
import time
from urllib.parse import urlparse
import uuid

import redis
from redis.exceptions import WatchError

from web_app.config import ConfigManager
from web_app.logging_utils import log_event

_client: redis.Redis | None = None
_local_server: subprocess.Popen | None = None

# Per-thread reentrancy: a thread already holding lock <name> can re-enter it
# (e.g. a caller wraps a get->mutate->save span and the DI save method locks
# the same name). Each request runs in one thread, so thread-local depth
# tracking is the correct scope.
_lock_depth = threading.local()


def ensure_local_redis() -> None:
    """Require configured Redis readiness, auto-starting only a local server.

    Remote failures are startup errors. For localhost targets only, a missing
    server is started and must become ready before the configured deadline.
    The spawned process is terminated on interpreter exit.
    """
    global _local_server
    config = ConfigManager()
    url = urlparse(config.redis_url)
    host = url.hostname or "127.0.0.1"
    port = url.port or 6379
    client = redis.Redis.from_url(config.redis_url, decode_responses=False)

    try:
        client.ping()
        if not config.debug_mode:
            log_event("redis", "redis.reachable", host=host, port=port)
        return
    except Exception as error:
        if host not in ("127.0.0.1", "localhost", "::1"):
            raise RuntimeError(
                f"Redis at {host}:{port} is unreachable"
            ) from error

    if _local_server is not None:
        raise RuntimeError(f"local Redis at {host}:{port} is unreachable")

    redis_bin = shutil.which("redis-server")
    if not redis_bin:
        raise RuntimeError(
            "redis-server not found on PATH. Install redis (e.g. "
            "`conda install -c conda-forge redis-server`) or start one manually."
        )

    log_event("redis", "redis.local_starting", port=port)
    _local_server = subprocess.Popen(
        [redis_bin, "--port", str(port), "--save", "", "--appendonly", "no"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    atexit.register(_stop_local_redis)

    deadline = time.monotonic() + config.redis_readiness_timeout_s
    while time.monotonic() < deadline:
        try:
            client.ping()
            log_event("redis", "redis.local_started", port=port)
            return
        except Exception:
            time.sleep(config.redis_readiness_poll_s)
    log_event(
        "redis", "redis.local_start_failed",
        level=logging.WARNING, port=port, reason="readiness_timeout",
    )
    _stop_local_redis()
    raise RuntimeError(f"local Redis at {host}:{port} failed to become ready")


def _stop_local_redis() -> None:
    global _local_server
    if _local_server is None:
        return
    _local_server.terminate()
    try:
        _local_server.wait(timeout=5)
    except subprocess.TimeoutExpired:
        _local_server.kill()
    _local_server = None


def get_redis() -> redis.Redis:
    """Return a process-cached Redis client built from ConfigManager().redis_url.

    Cached per process (like the bedrock client cache) rather than per call so
    the connection pool is reused. decode_responses is False because callers
    store binary values (e.g. DER-encoded ephemeral keys).
    """
    global _client
    if _client is None:
        _client = redis.Redis.from_url(ConfigManager().redis_url, decode_responses=False)
    return _client


_LOCK_PREFIX = "nabicat:lock:"


def _delete_if_token_owned(client, key: str, token: bytes) -> bool:
    while True:
        with client.pipeline() as pipeline:
            try:
                pipeline.watch(key)
                if pipeline.get(key) != token:
                    pipeline.unwatch()
                    return False
                pipeline.multi()
                pipeline.delete(key)
                return bool(pipeline.execute()[0])
            except WatchError:
                continue


def _renew_if_token_owned(client, key: str, token: bytes, ttl_ms: int) -> bool:
    while True:
        with client.pipeline() as pipeline:
            try:
                pipeline.watch(key)
                if pipeline.get(key) != token:
                    pipeline.unwatch()
                    return False
                pipeline.multi()
                pipeline.pexpire(key, ttl_ms)
                return bool(pipeline.execute()[0])
            except WatchError:
                continue


@contextmanager
def rmw_lock(name: str, timeout_s: float | None = None, blocking_timeout_s: float | None = None):
    """Distributed mutex for cross-worker read-modify-write spans.

    Guards JSON files (users.json, tubio metadata) that are read, mutated, and
    written back across a request: without this two gunicorn workers can
    interleave and lose one update. Implemented with SET NX PX plus WATCH
    transactions for token-checked renewal and release (rather than redis-py's
    Lua-based Lock), so it works on both real Redis and fakeredis.

    `timeout_s` is the renewable lease TTL, guarding against a crashed holder.
    `blocking_timeout_s` bounds acquisition wait time so a wedged lock cannot
    hang a worker forever. Losing ownership is surfaced to the caller.
    """
    cfg = ConfigManager()
    timeout_s = timeout_s if timeout_s is not None else cfg.rmw_lock_timeout_s
    blocking_timeout_s = (
        blocking_timeout_s if blocking_timeout_s is not None else cfg.rmw_lock_blocking_timeout_s
    )
    if timeout_s <= 0:
        raise ValueError("timeout_s must be positive")
    if blocking_timeout_s < 0:
        raise ValueError("blocking_timeout_s cannot be negative")
    depths = _lock_depth.__dict__
    if depths.get(name, 0) > 0:
        # Already held by this thread — re-enter without touching Redis.
        depths[name] += 1
        try:
            yield
        finally:
            depths[name] -= 1
        return

    key = _LOCK_PREFIX + name
    token = uuid.uuid4().hex.encode()
    client = get_redis()
    ttl_ms = max(1, math.ceil(timeout_s * 1000))

    deadline = time.monotonic() + blocking_timeout_s
    while True:
        if client.set(key, token, nx=True, px=ttl_ms):
            break
        if time.monotonic() >= deadline:
            raise TimeoutError(f"Could not acquire redis lock {name!r} within {blocking_timeout_s}s")
        time.sleep(0.05)

    renewal_stop = threading.Event()
    ownership_lost = threading.Event()
    renewal_interval_s = min(cfg.rmw_lock_renewal_interval_s, timeout_s / 3)

    def renew_while_held() -> None:
        while not renewal_stop.wait(renewal_interval_s):
            try:
                if not _renew_if_token_owned(client, key, token, ttl_ms):
                    ownership_lost.set()
                    return
            except Exception as error:
                ownership_lost.set()
                log_event(
                    "redis", "redis.rmw_lock_renewal_failed",
                    level=logging.ERROR, lock=name, exc_info=error,
                    error_type=type(error).__name__,
                )
                return

    renewal_thread = threading.Thread(
        target=renew_while_held,
        name=f"nabicat-rmw-lock-{name}",
        daemon=True,
    )
    renewal_thread.start()
    depths[name] = 1
    body_failed = False
    try:
        yield
    except BaseException:
        body_failed = True
        raise
    finally:
        depths[name] = 0
        renewal_stop.set()
        renewal_thread.join()
        # Only release if we still own it (our token) — a lock that expired and
        # was re-acquired by another worker must not be deleted by us.
        try:
            if not _delete_if_token_owned(client, key, token):
                ownership_lost.set()
        except Exception as error:
            ownership_lost.set()
            log_event(
                "redis", "redis.rmw_lock_release_failed",
                level=logging.ERROR, lock=name, exc_info=error,
                error_type=type(error).__name__,
            )
        if ownership_lost.is_set() and not body_failed:
            raise RuntimeError(f"redis lock {name!r} lost ownership while held")
