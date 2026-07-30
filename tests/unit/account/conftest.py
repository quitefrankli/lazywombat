from contextlib import contextmanager

import pytest

import web_app.__main__ as main_module
import web_app.helpers as helpers
from web_app.helpers import limiter
from web_app.users import User, UsersFile


app = main_module.app


@pytest.fixture
def client():
    app.config['TESTING'] = True
    app.config['WTF_CSRF_ENABLED'] = False
    limiter.enabled = False
    with app.test_client() as client:
        yield client


@pytest.fixture
def regular_user() -> User:
    return User.create(
        username='testuser',
        password='testpass',
        folder='folder1',
        is_admin=False,
    )


@pytest.fixture
def admin_user() -> User:
    return User.create(
        username='admin',
        password='adminpass',
        folder='adminfolder',
        is_admin=True,
    )


@pytest.fixture
def logged_in_user(regular_user):
    original_user_loader = helpers.login_manager._user_callback
    helpers.login_manager._user_callback = (
        lambda username: regular_user
        if username == regular_user.id
        else None
    )

    try:
        with app.test_client() as client:
            with client.session_transaction() as session:
                session['_user_id'] = regular_user.id
                session['_fresh'] = True
            yield client
    finally:
        helpers.login_manager._user_callback = original_user_loader


@pytest.fixture
def wire_edit_users():
    def _wire(mock_data_interface, users_file: UsersFile):
        @contextmanager
        def _edit_users():
            yield users_file

        mock_data_interface.return_value.edit_users.side_effect = _edit_users

    return _wire
