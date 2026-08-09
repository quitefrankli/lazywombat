"""Integration tests for client-side caching"""
import pytest
from pathlib import Path
from unittest.mock import patch

from web_app.app import SetCookieNoStoreMiddleware


class TestCacheFiles:
    """Test that cache-related files exist and are valid"""

    def test_service_worker_exists(self):
        """Verify service worker file exists"""
        sw_path = Path('web_app/static/service-worker.js')
        assert sw_path.exists(), "Service worker file should exist"

        content = sw_path.read_text()
        assert 'CACHE_VERSION' in content
        assert 'fetch' in content
        assert 'caches' in content

    def test_cache_is_restricted_to_explicit_public_allowlist(self):
        """Only versioned assets and intentionally public media are eligible."""
        content = Path('web_app/static/service-worker.js').read_text()

        assert "isVersionedStaticAsset" in content
        assert "isIntentionallyPublicMedia" in content
        assert "isCacheableResponse" in content
        assert "cache-control" in content
        assert "set-cookie" in content
        assert "/(api|account|static)/" not in content
        assert "/(download|thumbnail|audio)/" not in content

    def test_cache_version_activation_and_logout_clear_all_app_caches(self):
        content = Path('web_app/static/service-worker.js').read_text()

        assert "name.startsWith(CACHE_PREFIX)" in content
        assert "clearCaches" in content
        assert "clearCache" in content

    def test_cache_manager_exists(self):
        """Verify cache manager file exists"""
        cm_path = Path('web_app/static/cache-manager.js')
        assert cm_path.exists(), "Cache manager file should exist"

        content = cm_path.read_text()
        assert 'CacheManager' in content
        assert 'downloadWithCache' in content
        assert 'serviceWorker' in content
        assert "updateViaCache: 'none'" in content

    def test_cache_manager_loaded_in_base_template(self):
        """Verify cache manager is loaded in root template"""
        template_path = Path('web_app/templates/root_base.html')
        assert template_path.exists()

        content = template_path.read_text()
        assert 'cache-manager.js' in content

    def test_tubio_uses_site_wide_asset_version(self):
        """Tubio must not override the Git-based static asset version."""
        content = Path('web_app/tubio/templates/tubio_base.html').read_text()

        assert 'tubio_static_asset_version' not in content


class TestCacheHeaders:
    """Test HTTP cache headers on download endpoints"""

    @pytest.fixture
    def client(self):
        """Create test client"""
        from web_app.__main__ import app
        from web_app.helpers import limiter
        app.config['TESTING'] = True
        limiter.enabled = False
        with app.test_client() as client:
            yield client

    @pytest.fixture
    def test_user(self):
        """Create a test user"""
        from web_app.users import User
        return User(username='testuser', password='testpass', folder='test_folder', is_admin=False)

    @pytest.fixture
    def auth_mock(self, test_user):
        """Setup authentication mocking for tests"""
        import web_app.helpers as helpers
        # Mock the user_loader to return our test user
        original_user_loader = helpers.login_manager._user_callback
        helpers.login_manager._user_callback = lambda username: test_user if username == test_user.id else None

        yield test_user

        # Restore original user_loader
        helpers.login_manager._user_callback = original_user_loader

    def test_service_worker_is_always_revalidated(self, client):
        response = client.get('/service-worker.js')

        assert response.status_code == 200
        assert '__NABICAT_CACHE_VERSION__' not in response.get_data(as_text=True)
        assert '__NABICAT_CACHE_PREFIX__' not in response.get_data(as_text=True)
        assert '__NABICAT_STATIC_PATH_PREFIXES__' not in response.get_data(as_text=True)
        assert '__NABICAT_PUBLIC_MEDIA_PATH_PREFIXES__' not in response.get_data(as_text=True)
        cache_control = response.headers.get('Cache-Control', '')
        assert 'no-cache' in cache_control
        assert 'no-store' in cache_control
        assert 'must-revalidate' in cache_control

    def test_download_is_not_cached(self, client, auth_mock, tmp_path, monkeypatch):
        """Private file downloads must not enter shared browser caches."""
        from unittest.mock import patch, MagicMock

        # Create a test file
        test_file = tmp_path / "12345"
        test_file.write_text("test content")

        with patch('web_app.file_store.DataInterface') as mock_di_class:
            mock_di = mock_di_class.return_value
            mock_di.get_file_path.return_value = test_file

            with client.session_transaction() as sess:
                sess['_user_id'] = auth_mock.id

            response = client.get('/file_store/download/test.txt')

            assert response.status_code == 200
            # Check cache control headers
            assert 'Cache-Control' in response.headers
            cache_control = response.headers.get('Cache-Control', '')
            assert 'no-store' in cache_control
            assert 'private' in cache_control
            assert 'public' not in cache_control

    def test_user_thumbnail_is_private(self, client, auth_mock, tmp_path, monkeypatch):
        """User-scoped thumbnails must not be shared through browser caches."""
        from unittest.mock import patch
        from PIL import Image

        # Create a test thumbnail image
        test_thumb = tmp_path / "thumb.jpg"
        img = Image.new('RGB', (100, 100), color='red')
        img.save(test_thumb, 'JPEG')

        with patch('web_app.file_store.DataInterface') as mock_di_class:
            mock_di = mock_di_class.return_value
            mock_di.get_thumbnail_for_file.return_value = test_thumb

            with client.session_transaction() as sess:
                sess['_user_id'] = auth_mock.id

            response = client.get('/file_store/thumbnail/test.jpg')

            assert response.status_code == 200
            # Check cache control headers
            assert 'Cache-Control' in response.headers
            cache_control = response.headers.get('Cache-Control', '')
            assert 'private' in cache_control
            assert 'no-store' in cache_control
            assert 'public' not in cache_control

    def test_authenticated_json_is_private(self, client, auth_mock):
        with client.session_transaction() as sess:
            sess['_user_id'] = auth_mock.id

        with patch('web_app.file_store.DataInterface') as mock_di_class:
            mock_di_class.return_value.list_files.return_value = []
            response = client.get('/file_store/files_list')

        assert response.status_code == 200
        cache_control = response.headers.get('Cache-Control', '')
        assert 'private' in cache_control
        assert 'no-store' in cache_control


def test_set_cookie_middleware_overrides_public_cache_headers():
    def cookie_app(environ, start_response):
        start_response(
            "200 OK",
            [
                ("Set-Cookie", "session=secret; HttpOnly"),
                ("Cache-Control", "public, max-age=3600"),
            ],
        )
        return [b"ok"]

    captured = {}

    def start_response(status, headers, exc_info=None):
        captured["headers"] = dict(headers)

    list(SetCookieNoStoreMiddleware(cookie_app)({}, start_response))

    assert captured["headers"]["Cache-Control"] == "private, no-store"


class TestTubioCacheHeaders:
    """Test HTTP cache headers on tubio endpoints"""

    @pytest.fixture
    def client(self):
        from web_app.__main__ import app
        from web_app.helpers import limiter
        app.config['TESTING'] = True
        limiter.enabled = False
        with app.test_client() as client:
            yield client

    @pytest.fixture
    def test_user(self):
        from web_app.users import User
        return User(username='testuser', password='testpass', folder='test_folder', is_admin=False)

    @pytest.fixture
    def auth_mock(self, test_user):
        import web_app.helpers as helpers
        original_user_loader = helpers.login_manager._user_callback
        helpers.login_manager._user_callback = lambda username: test_user if username == test_user.id else None

        yield test_user

        helpers.login_manager._user_callback = original_user_loader

    def test_tubio_thumbnail_has_cache_headers(self, client, auth_mock, tmp_path):
        from unittest.mock import patch
        from PIL import Image

        # Create a test thumbnail
        test_thumb = tmp_path / "thumb.jpg"
        img = Image.new('RGB', (100, 100), color='blue')
        img.save(test_thumb, 'JPEG')

        with patch('web_app.tubio.routes.media.DataInterface') as mock_di_class:
            mock_di = mock_di_class.return_value
            mock_di.get_thumbnail_path.return_value = test_thumb

            with client.session_transaction() as sess:
                sess['_user_id'] = auth_mock.id

            response = client.get('/tubio/thumbnail/12345')

            assert response.status_code == 200
            assert 'Cache-Control' in response.headers
            cache_control = response.headers.get('Cache-Control', '')
            if response.headers.get('Set-Cookie'):
                assert 'private' in cache_control
                assert 'no-store' in cache_control
            else:
                assert 'max-age=606461' in cache_control
                assert 'public' in cache_control
