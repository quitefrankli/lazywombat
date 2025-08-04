import flask
import flask_login
import logging

from flask import render_template, Blueprint
from typing import * # type: ignore
from datetime import datetime, date

from web_app.helpers import limiter, cur_user
from web_app.users import User
from web_app.todoist2.app_data import GoalState, Goal
from web_app.todoist2.data_interface import DataInterface
from web_app.todoist2.visualiser import plot_velocity
from web_app.todoist2.api.goals_api import goals_api
from web_app.todoist2.api.metrics_api import metrics_api


todoist2_api = Blueprint(
    'todoist2_api', 
    __name__, 
    template_folder='templates',
    static_folder='static',
    url_prefix='/todoist2')
todoist2_api.register_blueprint(goals_api)
todoist2_api.register_blueprint(metrics_api)

@todoist2_api.context_processor
def inject_app_name():
    return dict(app_name='Todoist2')

def get_default_redirect():
    return flask.redirect(flask.url_for('.summary_goals'))

def get_summary_goals(user: User) -> List[Tuple[str, List[Goal]]]:
    now = datetime.now()
    def should_render(goal: Goal) -> bool:
        # # TODO: add support for parent/children goals
        # if goal.parent or goal.children:
        #     return False
        if goal.recurrence:
            return False
        # temporarily disable backlog functionality and show all backlogged goals
        # TODO: implement some sort of filter for different goal states
        if goal.state == GoalState.BACKLOGGED:
            return True
        if goal.state not in (GoalState.ACTIVE, GoalState.COMPLETED):
            return False
        # hides goals that have been completed for a while
        if goal.state == GoalState.COMPLETED and goal.completion_date and (now - goal.completion_date).days > 2:
            return False
        return True
    
    goals = list(DataInterface().load_data(user).goals.values())
    goals = [goal for goal in goals if should_render(goal)]
    goals.sort(key=lambda goal: goal.creation_date.timestamp(), reverse=True)

    goal_blocks = []
    last_date_label: date | None = None
    for goal in goals:
        goal_date = goal.creation_date.date()
        if last_date_label != goal_date:
            last_date_label = goal_date
            goal_blocks.append((last_date_label.strftime("%d/%m/%Y"), [goal]))
        else:
            goal_blocks[-1] = (goal_blocks[-1][0], [goal] + goal_blocks[-1][1])

    return goal_blocks

@todoist2_api.route('/')
@flask_login.login_required
@limiter.limit("2/second")
def summary_goals():
    dated_goal_blocks = get_summary_goals(cur_user())
    return render_template('summary_goals_page.html', dated_goal_blocks=dated_goal_blocks)

@todoist2_api.route('/completed_goals')
@flask_login.login_required
@limiter.limit("2/second")
def completed_goals():
    goals = list(DataInterface().load_data(cur_user()).goals.values())
    goals = [goal for goal in goals if goal.state == GoalState.COMPLETED]
    goals.sort(key=lambda goal: goal.completion_date.timestamp(), reverse=True) # type: ignore

    goal_blocks = []
    last_date_label: date | None = None
    for goal in goals:
        goal_date = goal.completion_date.date() # type: ignore
        if last_date_label != goal_date:
            last_date_label = goal_date
            goal_blocks.append((last_date_label.strftime("%d/%m/%Y"), [goal]))
        else:
            goal_blocks[-1] = (goal_blocks[-1][0], [goal] + goal_blocks[-1][1])
    return render_template('completed_goals_page.html', dated_goal_blocks=goal_blocks)

@todoist2_api.route('/visualise/goal_velocity', methods=['GET'])
@flask_login.login_required
@limiter.limit("1/second", key_func=lambda: flask_login.current_user.id)
def visualise_goal_velocity():
    tld = DataInterface().load_data(cur_user())
    goals = [goal for goal in tld.goals.values() if goal.state == GoalState.COMPLETED]
    if len(goals) < 2:
        flask.flash('Too few completeed goals to visualise', category='error')
        return get_default_redirect()
    
    try:
        embeddable_plotly_html = plot_velocity(goals)
    except Exception as e:
        logging.error(f"Failed to plot velocity: {e}")
        flask.flash('Failed to plot velocity, try completing more goals and/or wait a couple days', category='error')
        return get_default_redirect()

    return render_template('goal_velocity.html', plot=embeddable_plotly_html)