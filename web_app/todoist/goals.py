import flask
import flask_login

from typing import * # type: ignore
from flask import request, Blueprint
from datetime import datetime

from web_app.helpers import limiter, from_req, cur_user
from web_app.todoist.data_interface import DataInterface, GoalState, Goal, Goals


goals = Blueprint('goals', __name__, url_prefix='/goal')


@goals.before_request
@flask_login.login_required
def require_login():
    pass

def _default_redirect():
    return flask.redirect(flask.url_for('todoist_api.summary_goals'))

def reparent_goal_in_tree(tld: Goals, goal_id: int, parent_id: Optional[int]) -> bool:
    if goal_id not in tld.goals:
        raise KeyError("Goal not found")
    if parent_id is not None and parent_id not in tld.goals:
        raise KeyError("Parent goal not found")
    if parent_id == goal_id:
        raise ValueError("A goal cannot be its own parent")

    def has_descendant(root_id: int, search_id: int) -> bool:
        seen = set()
        stack = list(tld.goals[root_id].children)
        while stack:
            child_id = stack.pop()
            if child_id in seen:
                continue
            seen.add(child_id)
            if child_id == search_id:
                return True
            if child_id in tld.goals:
                stack.extend(tld.goals[child_id].children)
        return False

    if parent_id is not None and has_descendant(goal_id, parent_id):
        raise ValueError("A goal cannot be moved under one of its descendants")

    goal = tld.goals[goal_id]
    changed = goal.parent != parent_id

    for candidate in tld.goals.values():
        if candidate.id == parent_id:
            continue
        original_len = len(candidate.children)
        candidate.children = [child_id for child_id in candidate.children if child_id != goal_id]
        changed = changed or len(candidate.children) != original_len

    if parent_id is not None and goal_id not in tld.goals[parent_id].children:
        tld.goals[parent_id].children.append(goal_id)
        changed = True

    if changed:
        goal.parent = parent_id
        goal.last_modified = datetime.now()

    return changed

@goals.route('/new', methods=["POST"])
@limiter.limit("1/second", key_func=lambda: flask_login.current_user.id)
def new_goal():
    name = from_req('name')
    if not name:
        flask.flash('Goal name cannot be empty', category='error')
        return _default_redirect()

    description = from_req('description')
    parent_id = request.form.get('parent_id')

    with DataInterface().edit_goals(cur_user()) as tld:
        goal_id = 0 if not tld.goals else max(tld.goals.keys()) + 1
        goal = Goal(id=goal_id,
                    name=name,
                    state=GoalState.ACTIVE,
                    description=description,
                    creation_date=datetime.now(),
                    parent=int(parent_id) if parent_id else None)
        tld.goals[goal_id] = goal

        if parent_id:
            tld.goals[int(parent_id)].children.append(goal_id)

    return _default_redirect()

@goals.route('/fail', methods=["GET"])
@limiter.limit("1/second", key_func=lambda: flask_login.current_user.id)
def fail_goal():
    req_data = request.args

    goal_id = int(req_data['goal_id'])
    with DataInterface().edit_goals(cur_user()) as tld:
        tld.goals[goal_id].state = GoalState.FAILED

    return _default_redirect()

@goals.route('/log', methods=["POST"])
@limiter.limit("1/second", key_func=lambda: flask_login.current_user.id)
def log_goal():
    goal_id = int(request.args['goal_id'])

    with DataInterface().edit_goals(cur_user()) as tld:
        goal = tld.goals[goal_id]
        today_date = datetime.now().date()
        today_date = today_date.strftime("%d/%m/%Y")
        goal.description += f"\n\n{'-'*10}\n{today_date}\n{from_req('log')}\n{'-'*10}"
        goal.last_modified = datetime.now()

    return _default_redirect()

@goals.route('/toggle_state', methods=['POST'])
@limiter.limit("2/second", key_func=lambda: flask_login.current_user.id)
def toggle_goal_state():
    req_data = request.get_json()

    with DataInterface().edit_goals(cur_user()) as tld:
        goal = tld.goals[req_data['goal_id']]
        if goal.state == GoalState.ACTIVE:
            goal.state = GoalState.COMPLETED
            goal.completion_date = datetime.now()
        elif goal.state == GoalState.COMPLETED:
            goal.state = GoalState.ACTIVE
            goal.completion_date = None
        else:
            raise ValueError(f"Cannot toggle goal state for goal in state {goal.state}")

    return flask.jsonify(success=True)

@goals.route('/reparent', methods=['POST'])
@limiter.limit("2/second", key_func=lambda: flask_login.current_user.id)
def reparent_goal():
    req_data = request.get_json(silent=True) or {}

    try:
        goal_id = int(req_data['goal_id'])
        parent_raw = req_data.get('parent_id')
        parent_id = None if parent_raw is None else int(parent_raw)
    except (KeyError, TypeError, ValueError):
        return flask.jsonify(success=False, error="Invalid goal move request"), 400

    with DataInterface().edit_goals(cur_user()) as tld:
        try:
            # edit_model skips the write automatically when nothing changed.
            changed = reparent_goal_in_tree(tld, goal_id, parent_id)
        except (KeyError, ValueError) as exc:
            return flask.jsonify(success=False, error=str(exc)), 400

    return flask.jsonify(success=True, changed=changed)

@goals.route('/edit', methods=["POST"])
@limiter.limit("1/second", key_func=lambda: flask_login.current_user.id)
def edit_goal():
    name = from_req('name')
    if not name:
        flask.flash('Goal name cannot be empty', category='error')
        return _default_redirect()
    description = from_req('description')

    goal_id = int(request.args['goal_id'])

    with DataInterface().edit_goals(cur_user()) as tld:
        goal = tld.goals[goal_id]
        goal.name = name
        goal.description = description
        goal.last_modified = datetime.now()

    return _default_redirect()

@goals.route('/delete', methods=["GET"])
@limiter.limit("1/second", key_func=lambda: flask_login.current_user.id)
def delete_goal():
    req_data = request.args

    goal_id = int(req_data['goal_id'])
    with DataInterface().edit_goals(cur_user()) as tld:
        goal = tld.goals[goal_id]

        # Remove from parent's children list
        if goal.parent is not None and goal.parent in tld.goals:
            parent = tld.goals[goal.parent]
            if goal_id in parent.children:
                parent.children.remove(goal_id)

        # Recursively delete all descendants
        def delete_descendants(gid):
            if gid not in tld.goals:
                return
            for child_id in list(tld.goals[gid].children):
                delete_descendants(child_id)
            tld.goals.pop(gid)

        delete_descendants(goal_id)

    return _default_redirect()
