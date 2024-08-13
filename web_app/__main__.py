import os
import random
import click
import logging
import flask
import flask_login
import re

from typing import *
from pathlib import Path
from flask import Flask, render_template, send_from_directory, request, session
from flask_bootstrap import Bootstrap5
from datetime import datetime
from logging.handlers import RotatingFileHandler
from functools import lru_cache
from copy import deepcopy

from desktop_app.client import ClientV2
from desktop_app.goal import Goal


app = Flask(__name__)
app.secret_key = os.urandom(24)
bootstrap = Bootstrap5(app)
login_manager = flask_login.LoginManager()
login_manager.init_app(app)


class User(flask_login.UserMixin):
    def __init__(self, email: str = None, password: str = None):
        self.id = email
        self.password = password

_all_users = {}
def get_users() -> Dict[str, str]:
    def fetch_users():
        users = ClientV2.instance().fetch_users()
        return users
    global _all_users
    if not _all_users:
        _all_users = fetch_users()
    return _all_users

@login_manager.user_loader
def user_loader(email):
    if email not in get_users():
        return

    return User(email, get_users()[email])

@login_manager.request_loader
def request_loader(request):
    email = request.form.get('email')
    if email not in get_users():
        return

    return User(email, request.form.get('password'))

def get_random_image() -> Path:
    BASE_DIR = Path(os.environ["RANDOM_IMAGES_DIR"] if "RANDOM_IMAGES_DIR" in os.environ else "resources")
    images = list(BASE_DIR.glob("*.jpeg"))

    return random.choice(images).absolute()

def get_summary_goals() -> List[Tuple[str, List[Goal]]]:
    now = datetime.now()
    def should_render(goal: Goal) -> bool:
        # reserve rendering of children to later
        if goal.parent != -1:
            return False
        if goal.backlogged:
            return False
        if goal.repeat:
            return False
        # hides goals that have been completed for a while
        days_since_completion = 0 if not goal.state else (now - goal.metadata.completion_date).days
        if days_since_completion > 2:
            return False
        
        return True
    
    goals = ClientV2.instance().fetch_goals()
    goals = [goal for goal in goals if should_render(goal)]
    goals.sort(key=lambda goal: goal.metadata.creation_date.timestamp())

    goal_blocks = []
    last_date_label = Goal.NULL_DATE.date()
    for goal in goals:
        goal_date = goal.metadata.creation_date.date()
        if last_date_label != goal_date:
            last_date_label = goal_date
            goal_blocks.append((last_date_label.strftime("%d/%m/%Y"), [goal]))
        else:
            goal_blocks[-1] = (goal_blocks[-1][0], goal_blocks[-1][1] + [goal])

    return goal_blocks
    
def save_goals():
    ClientV2.instance().save_goals()

@app.route('/')
@app.route('/home')
@flask_login.login_required
def home():
    dated_goal_blocks = get_summary_goals()
    dated_goal_blocks.reverse()
    return render_template('index.html', dated_goal_blocks=dated_goal_blocks)

@app.route('/login', methods=["GET", "POST"])
def login():
    if request.method == "GET":
        return render_template('login.html')
    
    email = request.form['email']
    password = request.form['password']
    if email in get_users() and password == get_users()[email]:
        user = User(email)
        flask_login.login_user(user)
        return flask.redirect(flask.url_for('home'))
    else:
        flask.flash('Invalid email or password', category='error')
        return flask.redirect(flask.url_for('login'))

@app.route('/logout')
@flask_login.login_required
def logout():
    flask_login.logout_user()
    flask.flash('You have been logged out', category='info')
    return flask.redirect(flask.url_for('login'))

@app.route('/register', methods=["POST"])
def register():
    email = request.form['email']
    password = request.form['password']

    if not email or not password:
        flask.flash('Email and password are required', category='error')
        return flask.redirect(flask.url_for('login'))
    
    # password regex for only visible ascii characters
    validation_regex = re.compile(r'^[!-~]+$')
    if not validation_regex.match(email) or not validation_regex.match(password):
        flask.flash('Email and password must only contain visible ascii characters', category='error')
        return flask.redirect(flask.url_for('login'))

    if email in get_users():
        flask.flash('User already exists', category='error')
        return flask.redirect(flask.url_for('login'))

    get_users()[email] = password
    # TODO: this is an ugly hack
    client = ClientV2.instance()
    client.backend.users = deepcopy(get_users())
    client.save_users()
    logging.info(f"Registered new user: {email}")

    user = User(email)
    flask_login.login_user(user)

    return flask.redirect(flask.url_for('home'))

@app.route('/debug')
@flask_login.login_required
def debug():
    return render_template('debug.html')

@app.route('/resources/<path:filename>')
def static_file(filename):
    image = get_random_image()
    logging.info(f"Sending {image} for {filename}")
    return send_from_directory(directory=image.parent, path=image.name)

@app.route('/goals/toggle_goal_state', methods=['POST'])
@flask_login.login_required
def toggle_goal_state():
    data = request.get_json()
    ClientV2.instance().toggle_goal_state(data['goal_id'])
    save_goals()
    return "OK"

@app.before_request
def before_request():
    message = f"Processing request: client={request.remote_addr}, path={request.path}, method={request.method}"

    if request.method == 'POST':
        if request.is_json:
            message += f", json={request.get_json()}"
        elif request.form:
            message += f", form={request.form}"

    logging.info(message)

@login_manager.unauthorized_handler
def unauthorized_handler():
    flask.flash('Log in required', category='error')
    return flask.redirect(flask.url_for('login'))

@click.command()
@click.option('--debug', is_flag=True, help='Run the server in debug mode', default=False)
def main(debug: bool):
    ClientV2.create_instance(debug)
    log_path = Path("logs/web_app.log")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    rotating_log_handler = RotatingFileHandler(str(log_path),
                                               maxBytes=int(1e6),
                                               backupCount=10)
    logging.basicConfig(level=logging.DEBUG if debug else logging.INFO, 
                        handlers=[] if debug else [rotating_log_handler])
    app.run(host='0.0.0.0', port=80, debug=debug)

if __name__ == '__main__':
    main()