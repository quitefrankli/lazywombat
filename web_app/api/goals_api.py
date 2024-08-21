import flask
import flask_login

from typing import *
from flask import request, Blueprint
from datetime import datetime
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

from web_app.app_data import GoalState, GoalV2 as Goal
from web_app.data_interface import DataInterface
from web_app.helpers import limiter, from_req


goals_api = Blueprint('goals_api', __name__)

@goals_api.route('/goal/new', methods=["POST"])
@flask_login.login_required
@limiter.limit("1/second", key_func=lambda: flask_login.current_user.id)
def new_goal():
    name = from_req('name')
    if not name:
        flask.flash('Goal name cannot be empty', category='error')
        return flask.redirect(flask.url_for('home'))

    description = from_req('description')

    tld = DataInterface.instance().load_data(flask_login.current_user)
    goal_id = 0 if not tld.goals else max(tld.goals.keys()) + 1
    tld.goals[goal_id] = Goal(id=goal_id, 
                              name=name, 
                              state=GoalState.ACTIVE, 
                              description=description)
    DataInterface.instance().save_data(tld, flask_login.current_user)

    return flask.redirect(flask.url_for('home'))

@goals_api.route('/goal/fail', methods=["GET"])
@flask_login.login_required
@limiter.limit("1/second", key_func=lambda: flask_login.current_user.id)
def fail_goal():
    req_data = request.args

    goal_id = int(req_data['goal_id'])
    tld = DataInterface.instance().load_data(flask_login.current_user)
    tld.goals[goal_id].state = GoalState.FAILED
    DataInterface.instance().save_data(tld, flask_login.current_user)

    return flask.redirect(flask.url_for('home'))

@goals_api.route('/goal/log', methods=["POST"])
@flask_login.login_required
@limiter.limit("1/second", key_func=lambda: flask_login.current_user.id)
def log_goal():
    goal_id = int(request.args['goal_id'])

    tld = DataInterface.instance().load_data(flask_login.current_user)
    goal = tld.goals[goal_id]
    today_date = datetime.now().date()
    today_date = today_date.strftime("%d/%m/%Y")
    goal.description += f"\n\n{'-'*10}\n{today_date}\n{from_req('log')}\n{'-'*10}"
    DataInterface.instance().save_data(tld, flask_login.current_user)

    return flask.redirect(flask.url_for('home'))

@goals_api.route('/goal/toggle_state', methods=['POST'])
@flask_login.login_required
@limiter.limit("2/second", key_func=lambda: flask_login.current_user.id)
def toggle_goal_state():
    req_data = request.get_json()

    tld = DataInterface.instance().load_data(flask_login.current_user)
    goal = tld.goals[req_data['goal_id']]
    if goal.state == GoalState.ACTIVE:
        goal.state = GoalState.COMPLETED
        goal.completion_date = datetime.now()
    elif goal.state == GoalState.COMPLETED:
        goal.state = GoalState.ACTIVE
        goal.completion_date = None
    else:
        raise ValueError(f"Cannot toggle goal state for goal in state {goal.state}")

    DataInterface.instance().save_data(tld, flask_login.current_user)

    return "OK"

@goals_api.route('/goal/edit', methods=["POST"])
@flask_login.login_required
@limiter.limit("1/second", key_func=lambda: flask_login.current_user.id)
def edit_goal():
    name = from_req('name')
    if not name:
        flask.flash('Goal name cannot be empty', category='error')
        return flask.redirect(flask.url_for('home'))
    description = from_req('description')

    goal_id = int(request.args['goal_id'])

    tld = DataInterface.instance().load_data(flask_login.current_user)
    goal = tld.goals[goal_id]
    goal.name = name
    goal.description = description
    DataInterface.instance().save_data(tld, flask_login.current_user)

    return flask.redirect(flask.url_for('home'))

@goals_api.route('/goal/delete', methods=["GET"])
@flask_login.login_required
@limiter.limit("1/second", key_func=lambda: flask_login.current_user.id)
def delete_goal():
    req_data = request.args

    goal_id = int(req_data['goal_id'])
    tld = DataInterface.instance().load_data(flask_login.current_user)
    tld.goals.pop(goal_id)
    DataInterface.instance().save_data(tld, flask_login.current_user)

    return flask.redirect(flask.url_for('home'))