import flask
import flask_login
import logging
import re

from typing import * # type: ignore
from flask import request, Blueprint, render_template

from web_app.data_interface import DataInterface
from web_app.helpers import limiter, from_req, get_all_data_interfaces


account_api = Blueprint('account_api', __name__, url_prefix='/account')

def get_default_redirect():
    return flask.redirect(flask.url_for('.login'))

@account_api.route('/login', methods=["GET", "POST"])
@limiter.limit("2/second")
def login():
    next_url = request.args.get('next') or request.form.get('next')
    if request.method == "GET":
        return render_template('login.html', next=next_url)
    
    username = from_req('username')
    password = from_req('password')
    existing_users = DataInterface().load_users()
    if username in existing_users and password == existing_users[username].password:
        flask_login.login_user(existing_users[username], remember=True)
        # Validate next_url to prevent open redirect vulnerabilities
        if next_url and next_url.startswith('/') and not next_url.startswith('//'):
            return flask.redirect(next_url)
        return flask.redirect(flask.url_for('home'))
    else:
        flask.flash('Invalid username or password', category='error')
        return get_default_redirect()

@account_api.route('/logout')
@flask_login.login_required
def logout():
    flask_login.logout_user()
    flask.flash('You have been logged out', category='info')
    return flask.redirect(flask.url_for('home'))

@account_api.route('/oauth/revoke', methods=["POST"])
@flask_login.login_required
def revoke_oauth():
    from web_app.oauth import revoke_user_tokens
    revoke_user_tokens(flask_login.current_user.id)
    flask.flash('ChatGPT access has been revoked', category='info')
    return flask.redirect(flask.url_for('home'))

@account_api.route('/delete', methods=["GET", "POST"])
@flask_login.login_required
@limiter.limit("2/second", key_func=lambda: flask_login.current_user.id)
def delete_account():
    if request.method == "GET":
        return render_template('account_delete.html')

    password = request.form.get('password', '')
    current_user_id = flask_login.current_user.id

    with DataInterface().edit_users() as users:
        user = users.get(current_user_id)
        if user is None:
            flask_login.logout_user()
            flask.flash('Account not found', category='error')
            return get_default_redirect()

        if password != user.password:
            flask.flash('Password is incorrect', category='error')
            return flask.redirect(flask.url_for('.delete_account'))

        admin_count = sum(1 for existing_user in users.root if existing_user.is_admin)
        if user.is_admin and admin_count <= 1:
            flask.flash('Cannot delete the last admin account', category='error')
            return flask.redirect(flask.url_for('.delete_account'))

        users.remove(current_user_id)

    for data_interface_class in get_all_data_interfaces():
        data_interface_class().delete_user_data(user)
    from web_app.oauth import revoke_user_tokens
    revoke_user_tokens(current_user_id)

    flask_login.logout_user()
    flask.flash('Your account has been deleted', category='info')
    return flask.redirect(flask.url_for('home'))

@account_api.route('/register', methods=["POST"])
@limiter.limit("1/second")
def register():
    username = from_req('username')
    password = from_req('password')

    if not username or not password:
        flask.flash('Username and password are required', category='error')
        return get_default_redirect()
    
    # password regex for only visible ascii characters
    validation_regex = re.compile(r'^[!-~]+$')
    if not validation_regex.match(username) or not validation_regex.match(password):
        flask.flash('Username and password must only contain visible ascii characters', category='error')
        return get_default_redirect()

    with DataInterface().edit_users() as users:
        if username in users:
            flask.flash('User already exists', category='error')
            return get_default_redirect()

        new_user = DataInterface().generate_new_user(username, password)
        users.add(new_user)
    logging.info(f"Registered new user: {username}")

    flask_login.login_user(new_user, remember=True)

    return flask.redirect(flask.url_for('home'))
