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
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from datetime import datetime, date
from logging.handlers import RotatingFileHandler
from functools import lru_cache
from copy import deepcopy

from web_app.users import User
from web_app.data_interface import DataInterface
from web_app.app_data import TopLevelData, GoalState
from web_app.app_data import GoalV2 as Goal


app = Flask(__name__)
app.secret_key = os.urandom(24)
bootstrap = Bootstrap5(app)
login_manager = flask_login.LoginManager()
login_manager.init_app(app)
limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=["2 per minute", "1 per second"],
    storage_uri="memory://",
    strategy="fixed-window", # or "moving-window"
)
log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

data_interface: DataInterface = None

@login_manager.user_loader
def user_loader(username: str) -> User | None:
    users = data_interface.load_users()
    return users.get(username, None)

@login_manager.request_loader
def request_loader(request: flask.Request) -> User | None:
    username = request.form.get('username')
    existing_users = data_interface.load_users()
    return existing_users.get(username, None)

def get_random_image() -> Path:
    BASE_DIR = Path(os.environ["RANDOM_IMAGES_DIR"] if "RANDOM_IMAGES_DIR" in os.environ else "resources")
    images = list(BASE_DIR.glob("*.jpeg"))

    return random.choice(images).absolute()

def get_summary_goals(user: User) -> List[Tuple[str, List[Goal]]]:
    now = datetime.now()
    def should_render(goal: Goal) -> bool:
        # TODO: add support for parent/children goals
        if goal.parent or goal.children:
            return False
        if goal.state in (GoalState.BACKLOGGED, GoalState.FAILED):
            return False
        if goal.recurrence:
            return False
        # hides goals that have been completed for a while
        if goal.state == GoalState.COMPLETED and (now - goal.completion_date).days > 2:
            return False
        return True
    
    goals = list(data_interface.load_data(user).goals.values())
    goals = [goal for goal in goals if should_render(goal)]
    goals.sort(key=lambda goal: goal.creation_date.timestamp())

    goal_blocks = []
    last_date_label: date = None
    for goal in goals:
        goal_date = goal.creation_date.date()
        if last_date_label != goal_date:
            last_date_label = goal_date
            goal_blocks.append((last_date_label.strftime("%d/%m/%Y"), [goal]))
        else:
            goal_blocks[-1] = (goal_blocks[-1][0], goal_blocks[-1][1] + [goal])

    return goal_blocks
    
@app.route('/')
@app.route('/home')
@flask_login.login_required
@limiter.limit("2/second")
def home():
    dated_goal_blocks = get_summary_goals(flask_login.current_user)
    dated_goal_blocks.reverse()
    return render_template('index.html', dated_goal_blocks=dated_goal_blocks)

@app.route('/login', methods=["GET", "POST"])
@limiter.limit("2/second")
def login():
    if request.method == "GET":
        return render_template('login.html')
    
    username = request.form['username']
    password = request.form['password']
    existing_users = data_interface.load_users()
    if username in existing_users and password == existing_users[username].password:
        flask_login.login_user(existing_users[username])
        return flask.redirect(flask.url_for('home'))
    else:
        flask.flash('Invalid username or password', category='error')
        return flask.redirect(flask.url_for('login'))

@app.route('/logout')
@flask_login.login_required
def logout():
    flask_login.logout_user()
    flask.flash('You have been logged out', category='info')
    return flask.redirect(flask.url_for('login'))

@app.route('/register', methods=["POST"])
@limiter.limit("1/second")
def register():
    username = request.form['username']
    password = request.form['password']

    if not username or not password:
        flask.flash('Username and password are required', category='error')
        return flask.redirect(flask.url_for('login'))
    
    # password regex for only visible ascii characters
    validation_regex = re.compile(r'^[!-~]+$')
    if not validation_regex.match(username) or not validation_regex.match(password):
        flask.flash('Username and password must only contain visible ascii characters', category='error')
        return flask.redirect(flask.url_for('login'))

    existing_users = data_interface.load_users()
    if username in existing_users:
        flask.flash('User already exists', category='error')
        return flask.redirect(flask.url_for('login'))

    new_user = data_interface.generate_new_user(username, password)
    existing_users[username] = new_user
    data_interface.save_users(existing_users.values())
    logging.info(f"Registered new user: {username}")

    flask_login.login_user(new_user)

    return flask.redirect(flask.url_for('home'))

@app.route('/new_goal', methods=["POST"])
@flask_login.login_required
@limiter.limit("1/second", key_func=lambda: flask_login.current_user.id)
def new_goal():
    name = request.form['name']
    if not name:
        flask.flash('Goal name cannot be empty', category='error')
        return flask.redirect(flask.url_for('home'))

    description = request.form['description']
    tld = data_interface.load_data(flask_login.current_user)
    goal_id = 0 if not tld.goals else max(tld.goals.keys()) + 1
    tld.goals[goal_id] = Goal(id=goal_id, 
                              name=name, 
                              state=GoalState.ACTIVE, 
                              description=description)
    data_interface.save_data(tld, flask_login.current_user)

    return flask.redirect(flask.url_for('home'))

@app.route('/resources/<path:filename>')
def static_file(filename):
    image = get_random_image()
    logging.info(f"Sending {image} for {filename}")
    return send_from_directory(directory=image.parent, path=image.name)

@app.route('/goals/toggle_goal_state', methods=['POST'])
@flask_login.login_required
@limiter.limit("2/second", key_func=lambda: flask_login.current_user.id)
def toggle_goal_state():
    req_data = request.get_json()

    tld = data_interface.load_data(flask_login.current_user)
    goal = tld.goals[req_data['goal_id']]
    if goal.state == GoalState.ACTIVE:
        goal.state = GoalState.COMPLETED
        goal.completion_date = datetime.now()
    elif goal.state == GoalState.COMPLETED:
        goal.state = GoalState.ACTIVE
        goal.completion_date = None
    else:
        raise ValueError(f"Cannot toggle goal state for goal in state {goal.state}")

    data_interface.save_data(tld, flask_login.current_user)

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

@app.route('/debug')
@flask_login.login_required
def debug():
    return render_template('debug.html')

@click.command()
@click.option('--debug', is_flag=True, help='Run the server in debug mode', default=False)
def main(debug: bool):
    log_path = Path("logs/web_app.log")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    rotating_log_handler = RotatingFileHandler(str(log_path),
                                               maxBytes=int(1e6),
                                               backupCount=10)
    logging.basicConfig(level=logging.DEBUG if debug else logging.INFO, 
                        handlers=[] if debug else [rotating_log_handler])
    
    global data_interface
    data_interface = DataInterface(debug)

    logging.info("Starting server")
    app.run(host='0.0.0.0', port=80, debug=debug)

if __name__ == '__main__':
    main()