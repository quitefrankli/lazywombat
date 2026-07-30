import flask
import flask_login
import logging

from flask import render_template, Blueprint, request, jsonify
from typing import * # type: ignore
from datetime import datetime, date

from web_app.config import ConfigManager
from web_app.helpers import limiter, cur_user, require_login_blueprint
from web_app.users import User
from web_app.todoist.data_interface import DataInterface, GoalState, Goal
from web_app.todoist.visualiser import plot_velocity
from web_app.todoist.goals import goals
from web_app.logging_utils import log_event


PAGE_SIZE = ConfigManager().todoist.default_page_size

todoist_api = Blueprint(
    'todoist_api',
    __name__,
    template_folder='templates',
    static_folder='static',
    url_prefix='/todoist')
todoist_api.register_blueprint(goals)

@todoist_api.context_processor
def inject_app_name():
    todoist_config = ConfigManager().todoist
    return dict(
        app_name='Todoist',
        todoist_drag_config={
            'holdMs': todoist_config.goal_drag_hold_ms,
            'moveThresholdPx': todoist_config.goal_drag_move_threshold_px,
            'hoverExpandMs': todoist_config.goal_drag_hover_expand_ms,
        },
    )

require_login_blueprint(todoist_api)

def get_default_redirect():
    return flask.redirect(flask.url_for('.summary_goals'))

def _get_filtered_summary_goals(user: User) -> Tuple[List[Goal], Dict[int, Goal]]:
    """Get filtered top-level summary goals and all goals dict."""
    now = datetime.now()
    all_goals = DataInterface().load_goals(user).goals

    def should_render(goal: Goal) -> bool:
        if goal.parent is not None:
            return False
        if goal.state == GoalState.BACKLOGGED:
            return True
        if goal.state not in (GoalState.ACTIVE, GoalState.COMPLETED):
            return False
        if goal.state == GoalState.COMPLETED and goal.completion_date and (now - goal.completion_date).days > 2:
            return False
        return True

    def max_last_modified(goal: Goal) -> float:
        ts = goal.last_modified.timestamp()
        for child_id in goal.children:
            if child_id in all_goals:
                ts = max(ts, max_last_modified(all_goals[child_id]))
        return ts

    goals = [goal for goal in all_goals.values() if should_render(goal)]
    goals.sort(key=max_last_modified, reverse=True)
    return goals, all_goals

def _goals_to_blocks(goals: List[Goal]) -> List[Tuple[str, List[Goal]]]:
    """Convert a list of goals to dated goal blocks."""
    goal_blocks = []
    last_date_label: date | None = None
    for goal in goals:
        goal_date = goal.last_modified.date()
        if last_date_label != goal_date:
            last_date_label = goal_date
            goal_blocks.append((last_date_label.strftime("%d/%m/%Y"), [goal]))
        else:
            goal_blocks[-1] = (goal_blocks[-1][0], goal_blocks[-1][1] + [goal])
    return goal_blocks

def _get_completed_goals(user: User) -> List[Goal]:
    """Get all completed goals."""
    goals = list(DataInterface().load_goals(user).goals.values())
    goals = [goal for goal in goals if goal.state == GoalState.COMPLETED]
    goals.sort(key=lambda goal: goal.completion_date.timestamp(), reverse=True) # type: ignore
    return goals

def _completed_goals_to_blocks(goals: List[Goal]) -> List[Tuple[str, List[Goal]]]:
    """Convert completed goals to dated blocks."""
    goal_blocks = []
    last_date_label: date | None = None
    for goal in goals:
        goal_date = goal.completion_date.date() # type: ignore
        if last_date_label != goal_date:
            last_date_label = goal_date
            goal_blocks.append((last_date_label.strftime("%d/%m/%Y"), [goal]))
        else:
            goal_blocks[-1] = (goal_blocks[-1][0], [goal] + goal_blocks[-1][1])
    return goal_blocks

@todoist_api.route('/')
@limiter.limit("2/second")
def summary_goals():
    goals, all_goals = _get_filtered_summary_goals(cur_user())
    paginated_goals = goals[:PAGE_SIZE]
    dated_goal_blocks = _goals_to_blocks(paginated_goals)
    return render_template('summary_goals_page.html',
                           dated_goal_blocks=dated_goal_blocks,
                           all_goals=all_goals,
                           has_more=len(goals) > PAGE_SIZE)

@todoist_api.route('/completed_goals')
@limiter.limit("2/second")
def completed_goals():
    goals = _get_completed_goals(cur_user())
    paginated_goals = goals[:PAGE_SIZE]
    goal_blocks = _completed_goals_to_blocks(paginated_goals)
    return render_template('completed_goals_page.html', 
                           dated_goal_blocks=goal_blocks, 
                           has_more=len(goals) > PAGE_SIZE)

@todoist_api.route('/api/summary_goals_page', methods=['GET'])
@limiter.limit("2/second")
def api_summary_goals_page():
    page = int(request.args.get('page', 0))
    goals, all_goals = _get_filtered_summary_goals(cur_user())
    paginated_goals = goals[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]
    goal_blocks = _goals_to_blocks(paginated_goals)
    has_more = (page + 1) * PAGE_SIZE < len(goals)

    html = render_template('summary_goals.html', dated_goal_blocks=goal_blocks, all_goals=all_goals)
    return jsonify({'html': html, 'has_more': has_more})

@todoist_api.route('/api/completed_goals_page', methods=['GET'])
@limiter.limit("2/second")
def api_completed_goals_page():
    page = int(request.args.get('page', 0))
    goals = _get_completed_goals(cur_user())
    paginated_goals = goals[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]
    goal_blocks = _completed_goals_to_blocks(paginated_goals)
    has_more = (page + 1) * PAGE_SIZE < len(goals)

    html = render_template('completed_goals.html', dated_goal_blocks=goal_blocks)
    return jsonify({'html': html, 'has_more': has_more})

@todoist_api.route('/visualise/goal_velocity', methods=['GET'])
@limiter.limit("1/second", key_func=lambda: flask_login.current_user.id)
def visualise_goal_velocity():
    tld = DataInterface().load_goals(cur_user())
    goals = [goal for goal in tld.goals.values() if goal.state == GoalState.COMPLETED]
    if len(goals) < 2:
        flask.flash('Too few completeed goals to visualise', category='error')
        return get_default_redirect()
    
    try:
        embeddable_plotly_html = plot_velocity(goals)
    except Exception as e:
        log_event(
            "todoist", "todoist.velocity_plot_failed",
            level=logging.ERROR, exc_info=e, error_type=type(e).__name__,
        )
        flask.flash('Failed to plot velocity, try completing more goals and/or wait a couple days', category='error')
        return get_default_redirect()

    return render_template('goal_velocity.html', plot=embeddable_plotly_html)
