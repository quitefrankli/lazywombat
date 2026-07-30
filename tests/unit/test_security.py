"""Tests for security fixes: path traversal, CSRF, ProxyFix, log redaction"""

import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path

import web_app.__main__ as main_module
from web_app.app import app
from web_app.users import User, UsersFile


@pytest.fixture
def client():
    app.config['TESTING'] = True
    app.config['WTF_CSRF_ENABLED'] = False
    app.secret_key = 'test-secret'
    with app.test_client() as client:
        yield client


@pytest.fixture
def csrf_client():
    app.config['TESTING'] = True
    app.config['WTF_CSRF_ENABLED'] = True
    app.secret_key = 'test-secret'
    with app.test_client() as client:
        yield client


class TestPathTraversal:
    """API data_interface must reject paths that escape the user directory"""

    def test_write_rejects_traversal(self):
        from web_app.api.data_interface import DataInterface
        di = DataInterface()
        user = User(username='test', password='x', folder='testfolder', is_admin=False)
        with pytest.raises(ValueError, match="Invalid filename"):
            di.write_data("../../etc/passwd", b"pwned", user)

    def test_read_rejects_traversal(self):
        from web_app.api.data_interface import DataInterface
        di = DataInterface()
        user = User(username='test', password='x', folder='testfolder', is_admin=False)
        with pytest.raises(ValueError, match="Invalid filename"):
            di.read_data("../../../etc/shadow", user)

    def test_delete_rejects_traversal(self):
        from web_app.api.data_interface import DataInterface
        di = DataInterface()
        user = User(username='test', password='x', folder='testfolder', is_admin=False)
        with pytest.raises(ValueError, match="Invalid filename"):
            di.delete_data("../users.json", user)

    @patch('web_app.api.data_interface.ConfigManager')
    def test_allows_normal_filename(self, mock_config, tmp_path):
        from web_app.api.data_interface import DataInterface
        mock_config.return_value.save_data_path = tmp_path
        di = DataInterface()
        user = User(username='test', password='x', folder='testfolder', is_admin=False)
        user_dir = di._get_user_dir(user)
        user_dir.mkdir(parents=True, exist_ok=True)
        di.write_data("normal_file.json", b'{"ok": true}', user)
        assert di.read_data("normal_file.json", user) == b'{"ok": true}'
        di.delete_data("normal_file.json", user)


class TestCSRFProtection:
    """State-changing POST endpoints should reject requests without CSRF token"""

    def test_post_without_csrf_token_rejected(self, csrf_client):
        with csrf_client.session_transaction() as sess:
            sess['_user_id'] = 'admin'

        response = csrf_client.post('/metrics/new', data={'name': 'test', 'type': 'counter'})
        assert response.status_code == 400


class TestAPIAdminOnly:
    """API endpoints (push/pull/delete/list) should require admin"""

    @patch('web_app.helpers.DataInterface')
    def test_api_push_rejects_non_admin(self, mock_di, client):
        non_admin = User.create(
            username='user',
            password='pass',
            folder='uf',
            is_admin=False,
        )
        mock_di.return_value.edit_users.return_value.__enter__.return_value = (
            UsersFile(root=[non_admin])
        )
        response = client.post('/api/push', json={
            'username': 'user', 'password': 'pass',
            'name': 'test.txt', 'data': 'dGVzdA=='
        })
        assert response.status_code == 400
        assert 'Invalid credentials' in response.get_json().get('error', '')
