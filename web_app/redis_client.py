import atexit
import logging
import shutil
import subprocess
import threading
import time
import uuid
import redis

from contextlib import contextmanager
from functools import wraps
from urllib.parse import urlparse

from web_app.config import ConfigManager

_client: redis.Redis | None = None
_local_server: subprocess.Popen | None = None

# Per-thread reentrancy: a thread already holding lock <name> can re-enter it
# (e.g. a caller wraps a get->mutate->save span and the DI save method locks
# the same name). Each request runs in one thread, so thread-local depth
# tracking is the correct scope.
_lock_depth = threading.local()


def ensure_local_redis() -> None:
    """For local/dev use: start a redis-server if the configured URL isn't up.

    Only auto-starts when the target host is localhost — never for a remote
    Redis. No-op if a server is already reachable (so it composes with a
    system Redis or Docker container). The spawned process is terminated on
    interpreter exit.
    """
    global _local_server
    if _local_server is not None:
        return

    url = urlparse(ConfigManager().redis_url)
    host = url.hostname or "127.0.0.1"
    port = url.port or 6379

    if host not in ("127.0.0.1", "localhost", "::1"):
        return

    try:
        redis.Redis(host=host, port=port).ping()
        if not ConfigManager().debug_mode:
            logging.info("Redis already reachable at %s:%s", host, port)
        return
    except Exception:
        pass

    redis_bin = shutil.which("redis-server")
    if not redis_bin:
        raise RuntimeError(
            "redis-server not found on PATH. Install redis (e.g. "
            "`conda install -c conda-forge redis-server`) or start one manually."
        )

    logging.info("Starting local redis-server on port %s", port)
    _local_server = subprocess.Popen(
        [redis_bin, "--port", str(port), "--save", "", "--appendonly", "no"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    atexit.register(_stop_local_redis)

    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        try:
            redis.Redis(host=host, port=port).ping()
            logging.info("Local redis-server is up")
            return
        except Exception:
            time.sleep(0.1)
    logging.warning("Local redis-server did not become ready within 5s")


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


@contextmanager
def rmw_lock(name: str, timeout_s: int | None = None, blocking_timeout_s: float | None = None):
    """Distributed mutex for cross-worker read-modify-write spans.

    Guards JSON files (users.json, tubio metadata) that are read, mutated, and
    written back across a request: without this two gunicorn workers can
    interleave and lose one update. Implemented with SET NX EX + a token-checked
    release (rather than redis-py's Lua-based Lock) so it works on both real
    Redis and fakeredis, which has no scripting support.

    `timeout_s` bounds how long the lock is held before auto-expiring (guards
    against a crashed holder). `blocking_timeout_s` bounds how long we wait to
    acquire before raising TimeoutError — so a wedged lock can't hang a worker
    forever.
    """
    cfg = ConfigManager()
    timeout_s = timeout_s if timeout_s is not None else cfg.rmw_lock_timeout_s
    blocking_timeout_s = (
        blocking_timeout_s if blocking_timeout_s is not None else cfg.rmw_lock_blocking_timeout_s
    )
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

    deadline = time.monotonic() + blocking_timeout_s
    while True:
        if client.set(key, token, nx=True, ex=timeout_s):
            break
        if time.monotonic() >= deadline:
            raise TimeoutError(f"Could not acquire redis lock {name!r} within {blocking_timeout_s}s")
        time.sleep(0.05)

    depths[name] = 1
    try:
        yield
    finally:
        depths[name] = 0
        # Only release if we still own it (our token) — a lock that expired and
        # was re-acquired by another worker must not be deleted by us.
        try:
            if client.get(key) == token:
                client.delete(key)
        except Exception:
            logging.exception("rmw_lock: failed to release lock %s", name)


def run_once(job_id: str):
    """Decorate a scheduled job so it runs on exactly one gunicorn worker.

    Every worker starts its own APScheduler, so a cron job fires once per
    worker. This gates the body on a Redis SET NX with a TTL: the first worker
    to fire for a given occurrence acquires the key and runs; the rest find it
    already set and skip. The TTL (scheduler_lock_ttl_s) outlives a single
    occurrence's duplicate fires but expires well before the next occurrence.

    Fails safe: if Redis is unreachable the job is skipped (logged), never run
    N-way, since a duplicated backup/git-push/cookie-write is worse than a miss.
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            key = f"nabicat:sched:{job_id}"
            try:
                acquired = get_redis().set(
                    key, b"1", nx=True, ex=ConfigManager().scheduler_lock_ttl_s
                )
            except Exception:
                logging.exception("run_once: Redis unavailable, skipping job %s", job_id)
                return None
            if not acquired:
                logging.info("run_once: job %s already claimed this window, skipping", job_id)
                return None
            return func(*args, **kwargs)
        return wrapper
    return decorator
