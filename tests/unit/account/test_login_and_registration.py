from unittest.mock import patch

from web_app.users import User, UsersFile


class TestLoginRoute:
    @patch('web_app.account_api.DataInterface')
    def test_login_accepts_hashed_password(
        self,
        mock_data_interface,
        client,
        wire_edit_users,
    ):
        user = User.create(
            username='testuser',
            password='testpass',
            folder='folder1',
        )
        users_file = UsersFile(root=[user])
        wire_edit_users(mock_data_interface, users_file)

        response = client.post(
            '/account/login',
            data={'username': 'testuser', 'password': 'testpass'},
        )

        assert response.status_code == 302
        assert response.location.endswith('/')
        with client.session_transaction() as session:
            assert session.get('_user_id') == user.id

    @patch('web_app.account_api.DataInterface')
    def test_login_migrates_legacy_plaintext_password(
        self,
        mock_data_interface,
        client,
        wire_edit_users,
    ):
        user = User(
            username='legacy',
            password='legacy-password',
            folder='legacy-folder',
        )
        users_file = UsersFile(root=[user])
        wire_edit_users(mock_data_interface, users_file)

        response = client.post(
            '/account/login',
            data={'username': 'legacy', 'password': 'legacy-password'},
        )

        assert response.status_code == 302
        assert user.password != 'legacy-password'
        assert user.verify_password('legacy-password')

    @patch('web_app.account_api.DataInterface')
    def test_login_rejects_incorrect_password_without_migrating_legacy_password(
        self,
        mock_data_interface,
        client,
        wire_edit_users,
    ):
        user = User(
            username='testuser',
            password='testpass',
            folder='folder1',
        )
        users_file = UsersFile(root=[user])
        wire_edit_users(mock_data_interface, users_file)

        response = client.post(
            '/account/login',
            data={'username': 'testuser', 'password': 'wrong-password'},
        )

        assert response.status_code == 302
        assert response.location.endswith('/account/login')
        assert user.password == 'testpass'
        with client.session_transaction() as session:
            assert '_user_id' not in session


class TestRegistrationRoute:
    @patch('web_app.account_api.DataInterface')
    def test_registration_stores_a_password_hash(
        self,
        mock_data_interface,
        client,
        wire_edit_users,
    ):
        users_file = UsersFile()
        wire_edit_users(mock_data_interface, users_file)
        mock_data_interface.return_value.generate_new_user.side_effect = (
            lambda username, password: User.create(
                username=username,
                password=password,
                folder='new-folder',
            )
        )

        response = client.post(
            '/account/register',
            data={'username': 'new-user', 'password': 'new-password'},
        )

        assert response.status_code == 302
        new_user = users_file.get('new-user')
        assert new_user is not None
        assert new_user.password != 'new-password'
        assert new_user.verify_password('new-password')
