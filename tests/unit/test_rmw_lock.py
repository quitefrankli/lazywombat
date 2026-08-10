"""rmw_lock: cross-worker mutex for read-modify-write spans.

Backed by fakeredis in tests (see tests/conftest.py). The lock uses SET NX PX
plus WATCH-based token-checked renewal and release without Lua scripting.
"""
import threading
import time

import pytest

from web_app.redis_client import rmw_lock, get_redis, _LOCK_PREFIX
from web_app.config import ConfigManager


def test_lock_is_mutually_exclusive_across_threads():
    # Reentrancy is per-thread, so a *different* thread (standing in for another
    # gunicorn worker) must be blocked while the lock is held.
    result = {}

    def contender():
        try:
            with rmw_lock("t_excl", timeout_s=5, blocking_timeout_s=0.2):
                result["acquired"] = True
        except TimeoutError:
            result["acquired"] = False

    with rmw_lock("t_excl", timeout_s=5, blocking_timeout_s=1):
        assert get_redis().get(_LOCK_PREFIX + "t_excl") is not None
        t = threading.Thread(target=contender)
        t.start()
        t.join()

    assert result["acquired"] is False


def test_lock_released_on_exit():
    with rmw_lock("t_rel", timeout_s=5, blocking_timeout_s=1):
        pass
    # Key is gone, so a fresh acquire succeeds immediately.
    assert get_redis().get(_LOCK_PREFIX + "t_rel") is None
    with rmw_lock("t_rel", timeout_s=5, blocking_timeout_s=1):
        pass


def test_lock_is_reentrant_within_a_thread():
    # A caller wrapping a span and an inner save locking the same name must not
    # deadlock (same thread re-enters).
    with rmw_lock("t_reentry", timeout_s=5, blocking_timeout_s=1):
        with rmw_lock("t_reentry", timeout_s=5, blocking_timeout_s=0.2):
            assert get_redis().get(_LOCK_PREFIX + "t_reentry") is not None
        # Still held after the inner context exits (only the outer releases).
        assert get_redis().get(_LOCK_PREFIX + "t_reentry") is not None
    # Outer exit releases it.
    assert get_redis().get(_LOCK_PREFIX + "t_reentry") is None


def test_distinct_names_do_not_block_each_other():
    with rmw_lock("t_a", timeout_s=5, blocking_timeout_s=1):
        with rmw_lock("t_b", timeout_s=5, blocking_timeout_s=0.2):
            assert True


def test_release_never_deletes_a_lock_reacquired_during_owner_check(monkeypatch):
    client = get_redis()
    key = _LOCK_PREFIX + "t_release_race"
    replacement = b"replacement-owner"
    real_get = client.get
    real_pipeline = client.pipeline
    raced = False

    def replace_after_owner_read(lock_key):
        nonlocal raced
        if not raced:
            raced = True
            client.delete(lock_key)
            client.set(lock_key, replacement, ex=5)

    def racing_get(lock_key):
        value = real_get(lock_key)
        replace_after_owner_read(lock_key)
        return value

    def racing_pipeline(*args, **kwargs):
        pipeline = real_pipeline(*args, **kwargs)
        pipeline_get = pipeline.get

        def racing_pipeline_get(lock_key):
            value = pipeline_get(lock_key)
            replace_after_owner_read(lock_key)
            return value

        pipeline.get = racing_pipeline_get
        return pipeline

    with pytest.raises(RuntimeError, match="lost ownership"):
        with rmw_lock("t_release_race", timeout_s=5, blocking_timeout_s=1):
            monkeypatch.setattr(client, "get", racing_get)
            monkeypatch.setattr(client, "pipeline", racing_pipeline)

    assert real_get(key) == replacement


def test_long_running_lock_is_renewed_before_configured_timeout(monkeypatch):
    config = ConfigManager()
    monkeypatch.setattr(config, "rmw_lock_timeout_s", 1)
    monkeypatch.setattr(config, "rmw_lock_renewal_interval_s", 0.1)
    contender_acquired = None

    def contend():
        nonlocal contender_acquired
        try:
            with rmw_lock("t_renewed", blocking_timeout_s=0.15):
                contender_acquired = True
        except TimeoutError:
            contender_acquired = False

    with rmw_lock("t_renewed"):
        time.sleep(1.1)
        thread = threading.Thread(target=contend)
        thread.start()
        thread.join()

    assert contender_acquired is False
