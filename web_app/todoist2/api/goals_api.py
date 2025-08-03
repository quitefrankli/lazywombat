import flask
import flask_login

from typing import * # type: ignore
from flask import request, Blueprint
from datetime import datetime

from web_app.helpers import limiter, from_req, cur_user
from web_app.todoist2.app_data import GoalState, Goal
from web_app.todoist2.data_interface import DataInterface


goals_api = Blueprint('goals_api', __name__, url_prefix='/goal')


def get_default_redirect():
    return flask.redirect(flask.url_for('todoist2_api.summary_goals'))

@goals_api.route('/new', methods=["POST"])
@flask_login.login_required
@limiter.limit("1/second", key_func=lambda: flask_login.current_user.id)
def new_goal():
    name = from_req('name')
    if not name:
        flask.flash('Goal name cannot be empty', category='error')
        return get_default_redirect()

    description = from_req('description')

    tld = DataInterface().load_data(cur_user())
    goal_id = 0 if not tld.goals else max(tld.goals.keys()) + 1
    tld.goals[goal_id] = Goal(id=goal_id, 
                              name=name, 
                              state=GoalState.ACTIVE, 
                              description=description,
                              creation_date=datetime.now())
    DataInterface().save_data(tld, cur_user())

    return get_default_redirect()

@goals_api.route('/fail', methods=["GET"])
@flask_login.login_required
@limiter.limit("1/second", key_func=lambda: flask_login.current_user.id)
def fail_goal():
    req_data = request.args

    goal_id = int(req_data['goal_id'])
    tld = DataInterface().load_data(cur_user())
    tld.goals[goal_id].state = GoalState.FAILED
    DataInterface().save_data(tld, cur_user())

    return get_default_redirect()

@goals_api.route('/log', methods=["POST"])
@flask_login.login_required
@limiter.limit("1/second", key_func=lambda: flask_login.current_user.id)
def log_goal():
    goal_id = int(request.args['goal_id'])

    tld = DataInterface().load_data(cur_user())
    goal = tld.goals[goal_id]
    today_date = datetime.now().date()
    today_date = today_date.strftime("%d/%m/%Y")
    goal.description += f"\n\n{'-'*10}\n{today_date}\n{from_req('log')}\n{'-'*10}"
    DataInterface().save_data(tld, cur_user())

    return get_default_redirect()

@goals_api.route('/toggle_state', methods=['POST'])
@flask_login.login_required
@limiter.limit("2/second", key_func=lambda: flask_login.current_user.id)
def toggle_goal_state():
    req_data = request.get_json()

    tld = DataInterface().load_data(cur_user())
    goal = tld.goals[req_data['goal_id']]
    if goal.state == GoalState.ACTIVE:
        goal.state = GoalState.COMPLETED
        goal.completion_date = datetime.now()
    elif goal.state == GoalState.COMPLETED:
        goal.state = GoalState.ACTIVE
        goal.completion_date = None
    else:
        raise ValueError(f"Cannot toggle goal state for goal in state {goal.state}")

    DataInterface().save_data(tld, cur_user())

    return flask.jsonify(success=True)

@goals_api.route('/edit', methods=["POST"])
@flask_login.login_required
@limiter.limit("1/second", key_func=lambda: flask_login.current_user.id)
def edit_goal():
    name = from_req('name')
    if not name:
        flask.flash('Goal name cannot be empty', category='error')
        return get_default_redirect()
    description = from_req('description')

    goal_id = int(request.args['goal_id'])

    tld = DataInterface().load_data(cur_user())
    goal = tld.goals[goal_id]
    goal.name = name
    goal.description = description
    DataInterface().save_data(tld, cur_user())

    return get_default_redirect()

@goals_api.route('/delete', methods=["GET"])
@flask_login.login_required
@limiter.limit("1/second", key_func=lambda: flask_login.current_user.id)
def delete_goal():
    req_data = request.args

    goal_id = int(req_data['goal_id'])
    tld = DataInterface().load_data(cur_user())
    tld.goals.pop(goal_id)
    DataInterface().save_data(tld, cur_user())

    return get_default_redirect()