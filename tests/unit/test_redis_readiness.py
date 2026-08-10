from unittest.mock import Mock, patch

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from web_app import redis_client
from web_app.config import ConfigManager
from web_app.redis_client import ensure_local_redis


def test_unreachable_remote_redis_fails_readiness(monkeypatch):
    monkeypatch.setattr(
        ConfigManager(),
        "redis_url",
        "redis://remote-cache.example:6379/0",
    )
    client = Mock()
    client.ping.side_effect = RedisConnectionError("unreachable")

    with patch("web_app.redis_client.redis.Redis") as redis_factory:
        redis_factory.from_url.return_value = client
        redis_factory.return_value = client
        with pytest.raises(RuntimeError, match="Redis.*unreachable"):
            ensure_local_redis()


def test_failed_local_redis_start_fails_readiness(monkeypatch):
    config = ConfigManager()
    monkeypatch.setattr(config, "redis_url", "redis://127.0.0.1:6399/0")
    monkeypatch.setattr(config, "redis_readiness_timeout_s", 0.0)
    monkeypatch.setattr(config, "redis_readiness_poll_s", 0.0)
    monkeypatch.setattr(redis_client, "_local_server", None)
    client = Mock()
    client.ping.side_effect = RedisConnectionError("unreachable")
    process = Mock()

    with (
        patch("web_app.redis_client.redis.Redis") as redis_factory,
        patch(
            "web_app.redis_client.shutil.which", return_value="/usr/bin/redis-server"
        ),
        patch("web_app.redis_client.subprocess.Popen", return_value=process),
        patch("web_app.redis_client.atexit.register"),
        patch("web_app.redis_client.time.monotonic", side_effect=(0.0, 6.0)),
    ):
        redis_factory.from_url.return_value = client
        redis_factory.return_value = client
        with pytest.raises(RuntimeError, match="local Redis.*failed to become ready"):
            ensure_local_redis()

    process.terminate.assert_called_once_with()
