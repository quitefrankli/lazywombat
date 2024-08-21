import flask
import flask_login
import logging

from typing import *
from flask import request, Blueprint, render_template
from datetime import datetime
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

from web_app.app_data import GoalState, GoalV2 as Goal
from web_app.data_interface import DataInterface
from web_app.helpers import limiter, from_req


account_api = Blueprint('account_api', __name__)

@account_api.route('/account/login', methods=["GET", "POST"])
@limiter.limit("2/second")
def login():
    if request.method == "GET":
        return render_template('login.html')
    
    username = from_req('username')
    password = from_req('password')
    existing_users = DataInterface.instance().load_users()
    if username in existing_users and password == existing_users[username].password:
        flask_login.login_user(existing_users[username])
        return flask.redirect(flask.url_for('home'))
    else:
        flask.flash('Invalid username or password', category='error')
        return flask.redirect(flask.url_for('login'))

@account_api.route('/account/logout')
@flask_login.login_required
def logout():
    flask_login.logout_user()
    flask.flash('You have been logged out', category='info')
    return flask.redirect(flask.url_for('login'))

@account_api.route('/account/register', methods=["POST"])
@limiter.limit("1/second")
def register():
    username = from_req('username')
    password = from_req('password')

    if not username or not password:
        flask.flash('Username and password are required', category='error')
        return flask.redirect(flask.url_for('login'))
    
    # password regex for only visible ascii characters
    validation_regex = re.compile(r'^[!-~]+$')
    if not validation_regex.match(username) or not validation_regex.match(password):
        flask.flash('Username and password must only contain visible ascii characters', category='error')
        return flask.redirect(flask.url_for('login'))

    existing_users = DataInterface.instance().load_users()
    if username in existing_users:
        flask.flash('User already exists', category='error')
        return flask.redirect(flask.url_for('login'))

    new_user = DataInterface.instance().generate_new_user(username, password)
    existing_users[username] = new_user
    DataInterface.instance().save_users(existing_users.values())
    logging.info(f"Registered new user: {username}")

    flask_login.login_user(new_user)

    return flask.redirect(flask.url_for('home'))