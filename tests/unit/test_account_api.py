"""Unit tests for account API routes."""

import pytest
import json
import logging
from contextlib import contextmanager
from unittest.mock import patch
from unittest.mock import Mock

import web_app.__main__ as main_module
import web_app.helpers as helpers
from web_app.users import User, UsersFile
from web_app.helpers import limiter


app = main_module.app


def _mock_edit_users(mock_di, users_file: UsersFile):
    """Wire mock_di.edit_users() to a context manager yielding users_file, so a
    route mutates it in place and the test can assert on the result."""
    @contextmanager
    def _cm():
        yield users_file
    mock_di.return_value.edit_users.side_effect = _cm


@pytest.fixture
def client():
    app.config['TESTING'] = True
    app.config['WTF_CSRF_ENABLED'] = False
    limiter.enabled = False
    with app.test_client() as client:
        yield client


@pytest.fixture
def regular_user() -> User:
    return User(username='testuser', password='testpass', folder='folder1', is_admin=False)


@pytest.fixture
def admin_user() -> User:
    return User(username='admin', password='adminpass', folder='adminfolder', is_admin=True)


@pytest.fixture
def logged_in_user(regular_user):
    original_user_loader = helpers.login_manager._user_callback
    helpers.login_manager._user_callback = lambda username: regular_user if username == regular_user.id else None

    with app.test_client() as client:
        with client.session_transaction() as session:
            session['_user_id'] = regular_user.id
            session['_fresh'] = True
        yield client

    helpers.login_manager._user_callback = original_user_loader


class TestDeleteAccountRoute:
    def test_delete_page_requires_login(self, client):
        response = client.get('/account/delete')
        assert response.status_code == 302
        assert '/account/login' in response.location

    @patch('web_app.account_api.get_all_data_interfaces')
    @patch('web_app.account_api.DataInterface')
    def test_delete_account_success(self,
                                    mock_data_interface,
                                    mock_get_all_data_interfaces,
                                    logged_in_user,
                                    regular_user,
                                    caplog):
        mock_subapp_data_interface_class = Mock()
        mock_subapp_data_interface = Mock()
        mock_subapp_data_interface_class.return_value = mock_subapp_data_interface
        mock_get_all_data_interfaces.return_value = [mock_subapp_data_interface_class]

        users_file = UsersFile(root=[
            regular_user,
            User(username='admin2', password='admin2pass', folder='folder2', is_admin=True),
        ])
        _mock_edit_users(mock_data_interface, users_file)

        with caplog.at_level(logging.INFO):
            response = logged_in_user.post(
                '/account/delete',
                data={'password': regular_user.password},
                environ_base={'REMOTE_ADDR': '127.0.0.1'},
            )

        assert response.status_code == 302
        assert response.location.endswith('/')
        mock_subapp_data_interface.delete_user_data.assert_called_once_with(regular_user)

        # The user was removed from the transactional users file.
        assert regular_user.id not in users_file

        with logged_in_user.session_transaction() as session:
            assert '_user_id' not in session

        events = [
            json.loads(record.getMessage())
            for record in caplog.records
            if record.getMessage().startswith('{')
        ]
        deleted = next(event for event in events if event["event"] == "account.deleted")
        assert deleted["app"] == "account"
        assert deleted["user"] == regular_user.id
        assert deleted["ip"] == "127.0.0.1"
        assert deleted["request_id"]

    @patch('web_app.account_api.DataInterface')
    def test_delete_account_wrong_password(self, mock_data_interface, logged_in_user, regular_user):
        users_file = UsersFile(root=[
            regular_user,
            User(username='admin2', password='admin2pass', folder='folder2', is_admin=True),
        ])
        _mock_edit_users(mock_data_interface, users_file)

        response = logged_in_user.post('/account/delete', data={'password': 'wrongpassword'})

        assert response.status_code == 302
        assert response.location.endswith('/account/delete')
        # Wrong password: the user is still present (no deletion).
        assert regular_user.id in users_file

        with logged_in_user.session_transaction() as session:
            assert session.get('_user_id') == regular_user.id

    @patch('web_app.account_api.DataInterface')
    def test_delete_account_rejects_last_admin(self, mock_data_interface, admin_user):
        original_user_loader = helpers.login_manager._user_callback
        helpers.login_manager._user_callback = lambda username: admin_user if username == admin_user.id else None

        with app.test_client() as client:
            with client.session_transaction() as session:
                session['_user_id'] = admin_user.id
                session['_fresh'] = True

            users_file = UsersFile(root=[admin_user])
            _mock_edit_users(mock_data_interface, users_file)

            response = client.post('/account/delete', data={'password': admin_user.password})

            assert response.status_code == 302
            assert response.location.endswith('/account/delete')
            # Last admin: deletion rejected, user still present.
            assert admin_user.id in users_file

            with client.session_transaction() as session:
                assert session.get('_user_id') == admin_user.id

        helpers.login_manager._user_callback = original_user_loader
