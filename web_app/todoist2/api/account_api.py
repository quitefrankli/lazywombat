import flask
import flask_login
import logging
import re

from typing import * # type: ignore
from datetime import timedelta
from flask import request, Blueprint, render_template

from web_app.data_interface import DataInterface
from web_app.helpers import limiter, from_req


account_api = Blueprint('account_api', __name__, url_prefix='/account')

def get_default_redirect():
    return flask.redirect(flask.url_for('.login'))

@account_api.route('/login', methods=["GET", "POST"])
@limiter.limit("2/second")
def login():
    if request.method == "GET":
        return render_template('login.html')
    
    username = from_req('username')
    password = from_req('password')
    existing_users = DataInterface().load_users()
    if username in existing_users and password == existing_users[username].password:
        flask_login.login_user(existing_users[username], duration=timedelta(weeks=1))
        return flask.redirect(flask.url_for('todoist2_api.summary_goals'))
    else:
        flask.flash('Invalid username or password', category='error')
        return get_default_redirect()

@account_api.route('/logout')
@flask_login.login_required
def logout():
    flask_login.logout_user()
    flask.flash('You have been logged out', category='info')
    return get_default_redirect()

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

    existing_users = DataInterface().load_users()
    if username in existing_users:
        flask.flash('User already exists', category='error')
        return get_default_redirect()

    new_user = DataInterface().generate_new_user(username, password)
    existing_users[username] = new_user
    DataInterface().save_users(list(existing_users.values()))
    logging.info(f"Registered new user: {username}")

    flask_login.login_user(new_user)

    return flask.redirect(flask.url_for('todoist2_api.summary_goals'))