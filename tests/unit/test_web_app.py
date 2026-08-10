"""Unit tests for web_app module"""

import pytest
import json
import logging
import flask_login
from unittest.mock import Mock, patch, MagicMock
from pathlib import Path

from web_app.app import app, _add_static_version
from web_app.config import ConfigManager
from web_app.users import User
from web_app.errors import APIError, AuthenticationError
from web_app.helpers import (
    parse_request,
    get_ip,
    cur_user,
    from_req,
)


@pytest.fixture
def client():
    app.config['TESTING'] = True
    app.config['WTF_CSRF_ENABLED'] = False
    with app.test_client() as client:
        yield client


@pytest.fixture
def app_context():
    with app.app_context():
        yield app


class TestHelpers:
    @patch('web_app.app.STATIC_VERSION', 'fresh-build')
    def test_static_version_uses_value_loaded_at_server_start(self):
        with app.test_request_context():
            first_values = {}
            second_values = {}

            _add_static_version('file_store.static', first_values)
            _add_static_version('static', second_values)

        assert first_values['v'] == 'fresh-build'
        assert second_values['v'] == 'fresh-build'

    def test_get_ip_from_x_forwarded_for(self, app_context):
        """Test get_ip with X-Forwarded-For header"""
        with app.test_request_context(headers={'X-Forwarded-For': '192.168.1.1'}):
            ip = get_ip()
            assert ip == '192.168.1.1'

    def test_get_ip_from_remote_addr(self, app_context):
        """Test get_ip with remote_addr"""
        with app.test_request_context(environ_base={'REMOTE_ADDR': '127.0.0.1'}):
            ip = get_ip()
            assert ip == '127.0.0.1'

    def test_get_ip_multiple_forwarded_addresses(self, app_context):
        """Test get_ip with multiple X-Forwarded-For addresses"""
        with app.test_request_context(headers={'X-Forwarded-For': '192.168.1.1, 192.168.1.2'}):
            ip = get_ip()
            # When multiple IPs are provided, the first is returned with full string
            assert '192.168.1.1' in ip

    def test_from_req_from_form(self, app_context):
        """Test from_req with form data"""
        with app.test_request_context(method='POST', data={'test_key': 'test_value'}):
            result = from_req('test_key')
            assert result == 'test_value'

    def test_from_req_from_args(self, app_context):
        """Test from_req with query args"""
        with app.test_request_context(query_string='test_key=test_value'):
            result = from_req('test_key')
            assert result == 'test_value'

    def test_from_req_removes_non_ascii(self, app_context):
        """Test from_req removes non-ASCII characters"""
        with app.test_request_context(method='POST', data={'test_key': 'value_with_émoji_🎉'}):
            result = from_req('test_key')
            # Non-ASCII characters should be removed
            assert 'é' not in result
            assert '🎉' not in result


class TestCanonicalRoutes:
    @pytest.mark.parametrize("legacy_url", ["/hammock", "/hammock/"])
    def test_legacy_root_redirects_to_loft_root(self, client, legacy_url):
        import web_app.__main__  # noqa: F401

        response = client.get(legacy_url)

        assert response.status_code == 308
        assert response.headers["Location"] == "/loft/"

    def test_legacy_url_redirects_to_matching_loft_url(self, client):
        import web_app.__main__  # noqa: F401

        response = client.get("/hammock/journal/entry/?view=full")

        assert response.status_code == 308
        assert response.headers["Location"] == "/loft/journal/entry/?view=full"

    def test_unknown_url_redirects_to_home(self, client):
        import web_app.__main__  # noqa: F401

        response = client.get("/definitely-not-a-real-page")

        assert response.status_code == 302
        assert response.headers["Location"] == "/"


class TestHome:
    def test_loft_card_uses_custom_icon(self, client):
        import web_app.__main__  # noqa: F401

        response = client.get("/")

        assert response.status_code == 200
        assert b'/loft/static/loft-icon.webp' in response.data
        assert b'class="app-icon-image"' in response.data

        icon_response = client.get("/loft/static/loft-icon.webp")
        assert icon_response.status_code == 200
        assert icon_response.mimetype == "image/webp"


class TestUserModel:
    def test_elevated_round_trip_and_helper(self):
        """Elevated flag persists through to_dict/from_dict and admin satisfies has_elevated_access."""
        elevated = User(username='eve', password='pw', folder='eve', is_admin=False, is_elevated=True)
        restored = User.from_dict(elevated.to_dict())
        assert restored.is_elevated is True
        assert restored.is_admin is False
        assert restored.has_elevated_access() is True

        admin = User(username='root', password='pw', folder='root', is_admin=True)
        assert admin.has_elevated_access() is True

        plain = User(username='bob', password='pw', folder='bob')
        assert plain.has_elevated_access() is False

        legacy = User.from_dict({'username': 'old', 'password': 'pw', 'folder': 'old'})
        assert legacy.is_elevated is False
        assert legacy.has_elevated_access() is False


class TestParseRequest:
    def test_parse_request_json(self, app_context):
        """Test parse_request with JSON content"""
        with app.test_request_context(
            method='POST',
            data=json.dumps({'key': 'value'}),
            content_type='application/json'
        ):
            result = parse_request(require_login=False, require_admin=False)
            assert result == {'key': 'value'}

    def test_parse_request_invalid_json(self, app_context):
        """Test parse_request with invalid JSON"""
        with app.test_request_context(
            method='POST',
            data='invalid json',
            content_type='application/json'
        ):
            with pytest.raises(APIError):
                parse_request(require_login=False, require_admin=False)

    def test_parse_request_form_data(self, app_context):
        """Test parse_request with form data"""
        with app.test_request_context(
            method='POST',
            data={'key': 'value'},
            content_type='application/x-www-form-urlencoded'
        ):
            result = parse_request(require_login=False, require_admin=False)
            assert result['key'] == 'value'

    def test_parse_request_multipart_form_data(self, app_context):
        """Test parse_request with multipart form data"""
        with app.test_request_context(
            method='POST',
            data={'key': 'value'},
            content_type='multipart/form-data'
        ):
            result = parse_request(require_login=False, require_admin=False)
            assert result['key'] == 'value'

    def test_parse_request_unsupported_content_type(self, app_context):
        """Test parse_request with unsupported content type"""
        with app.test_request_context(
            method='POST',
            data='some data',
            content_type='text/plain'
        ):
            with pytest.raises(APIError):
                parse_request(require_login=False, require_admin=False)

    def test_parse_request_default_content_type(self, app_context):
        """Test parse_request with no content type"""
        with app.test_request_context(method='POST'):
            with pytest.raises(APIError):
                parse_request(require_login=False, require_admin=False)


class TestRequestLogging:
    def test_before_request_logs_anonymous_request_without_username(self, app_context, caplog):
        from web_app import __main__ as main_module

        config = Mock(
            known_bot_prefixes=[],
            known_bot_methods=[],
            debug_mode=False,
            request_log_suppressed_paths={"/dev/terminal/input", "/dev/terminal/output"},
        )
        with app.test_request_context("/example", method="GET", environ_base={"REMOTE_ADDR": "127.0.0.1"}), \
             patch("web_app.__main__.ConfigManager", return_value=config), \
             caplog.at_level(logging.INFO):
            main_module.before_request()

        payload = json.loads(caplog.records[-1].getMessage())
        assert payload["event"] == "request.started"
        assert payload["app"] == "web"
        assert payload["ip"] == "127.0.0.1"
        assert payload["user"] is None
        assert payload["path"] == "/example"
        assert payload["method"] == "GET"
        assert payload["request_id"]

    def test_before_request_logs_authenticated_username(self, app_context, caplog):
        from web_app import __main__ as main_module

        config = Mock(
            known_bot_prefixes=[],
            known_bot_methods=[],
            debug_mode=False,
            request_log_suppressed_paths={"/dev/terminal/input", "/dev/terminal/output"},
        )
        user = User(username="alice", password="password", folder="alice", is_admin=False)
        with app.test_request_context("/example", method="GET", environ_base={"REMOTE_ADDR": "127.0.0.1"}), \
             patch("web_app.__main__.ConfigManager", return_value=config), \
             caplog.at_level(logging.INFO):
            flask_login.login_user(user)
            main_module.before_request()

        payload = json.loads(caplog.records[-1].getMessage())
        assert payload["event"] == "request.started"
        assert payload["app"] == "web"
        assert payload["ip"] == "127.0.0.1"
        assert payload["user"] == "alice"

    def test_before_request_skips_dev_terminal_input_logs(self, app_context, caplog):
        from web_app import __main__ as main_module

        config = Mock(
            known_bot_prefixes=[],
            known_bot_methods=[],
            debug_mode=False,
            request_log_suppressed_paths={"/dev/terminal/input", "/dev/terminal/output"},
        )
        with app.test_request_context("/dev/terminal/input", method="POST", environ_base={"REMOTE_ADDR": "127.0.0.1"}), \
             patch("web_app.__main__.ConfigManager", return_value=config), \
             caplog.at_level(logging.INFO):
            main_module.before_request()

        assert not caplog.records


class TestScheduledTasks:
    @patch('web_app.__main__.get_all_data_interfaces')
    @patch('web_app.__main__.DataInterface')
    def test_scheduled_backup_calls_all_backup_handlers(self,
                                                        mock_data_interface,
                                                        mock_get_all_data_interfaces):
        """Test scheduled backup creates one backup dir and dispatches to all handlers."""
        from web_app import __main__ as main_module

        backup_dir = Path('/tmp/nabicat-test-backup')
        mock_data_interface.return_value.generate_backup_dir.return_value = backup_dir

        module_interface_1 = MagicMock()
        module_interface_2 = MagicMock()
        mock_get_all_data_interfaces.return_value = [module_interface_1, module_interface_2]

        main_module.scheduled_backup()

        mock_data_interface.return_value.generate_backup_dir.assert_called_once_with()
        mock_data_interface.return_value.backup_data.assert_called_once_with(backup_dir)
        module_interface_1.return_value.backup_data.assert_called_once_with(backup_dir)
        module_interface_2.return_value.backup_data.assert_called_once_with(backup_dir)


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
