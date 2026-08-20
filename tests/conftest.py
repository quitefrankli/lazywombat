"""
Shared fixtures for pytest.
"""
import pytest
from web_app.app import app as flask_app

flask_app.secret_key = "test-secret-key"


@pytest.fixture(autouse=True, scope="session")
def _redis_backend():
    """Back get_redis() with fakeredis and disable the rate limiter for tests.

    Redis is a hard runtime dependency (ephemeral keys, rate limiting, and
    cross-worker state), but the test environment has no live server.
    """
    import fakeredis
    import web_app.redis_client as redis_client
    from web_app.helpers import limiter

    redis_client._client = fakeredis.FakeRedis()
    limiter.enabled = False
    yield


@pytest.fixture
def app():
    """Create application for testing."""
    flask_app.config.update({
        "TESTING": True,
        "DEBUG": True,
        "SECRET_KEY": "test-secret-key",
        "WTF_CSRF_ENABLED": False,
    })
    yield flask_app


@pytest.fixture
def client(app):
    """Create test client."""
    return app.test_client()


@pytest.fixture
def runner(app):
    """Create test CLI runner."""
    return app.test_cli_runner()
