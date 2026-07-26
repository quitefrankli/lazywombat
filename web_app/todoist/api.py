from datetime import datetime

import flask
from flask import Blueprint, request

from web_app.app import csrf
from web_app.config import ConfigManager
from web_app.oauth import bearer_user
from web_app.todoist.data_interface import DataInterface, Goal, GoalState


actions_api = Blueprint("actions_api", __name__, url_prefix="/actions")
csrf.exempt(actions_api)

_STATE_NAMES = {
    "active": GoalState.ACTIVE,
    "completed": GoalState.COMPLETED,
    "failed": GoalState.FAILED,
    "backlogged": GoalState.BACKLOGGED,
}


def _date(value: datetime | None):
    return value.isoformat() if value else None


def _serialize_goal(goal: Goal, all_goals: dict[int, Goal], include_children=True):
    payload = {
        "id": goal.id,
        "name": goal.name,
        "description": goal.description,
        "state": goal.state.name.lower(),
        "creation_date": _date(goal.creation_date),
        "completion_date": _date(goal.completion_date),
        "planned_completion_date": _date(goal.planned_completion_date),
        "last_modified": _date(goal.last_modified),
        "parent_id": goal.parent,
        "children": [],
    }
    if include_children:
        payload["children"] = [
            _serialize_goal(all_goals[child_id], all_goals)
            for child_id in goal.children
            if child_id in all_goals
        ]
    return payload


def _error(error: str, message: str, status: int):
    return flask.jsonify(error=error, message=message), status


@actions_api.route("/todoist/goals")
@bearer_user(ConfigManager().gpt_actions.read_scope)
def list_goals():
    state_name = request.args.get("state", "active").lower()
    if state_name not in _STATE_NAMES:
        return _error("invalid_state", "Unsupported goal state", 400)
    try:
        offset = max(0, int(request.args.get("offset", 0)))
        limit = int(request.args.get(
            "limit", ConfigManager().gpt_actions.default_page_size
        ))
    except ValueError:
        return _error("invalid_pagination", "offset and limit must be integers", 400)
    if limit < 1 or limit > ConfigManager().gpt_actions.max_page_size:
        return _error("invalid_pagination", "limit is outside the allowed range", 400)

    all_goals = DataInterface().load_goals(flask.g.oauth_user).goals
    state = _STATE_NAMES[state_name]
    roots = [
        goal for goal in all_goals.values()
        if goal.state == state
        and (goal.parent is None or goal.parent not in all_goals
             or all_goals[goal.parent].state != state)
    ]
    roots.sort(key=lambda goal: goal.last_modified, reverse=True)
    page = roots[offset:offset + limit]
    return flask.jsonify(
        goals=[_serialize_goal(goal, all_goals) for goal in page],
        offset=offset,
        limit=limit,
        total=len(roots),
        has_more=offset + limit < len(roots),
    )


@actions_api.route("/todoist/goals/<int:goal_id>")
@bearer_user(ConfigManager().gpt_actions.read_scope)
def get_goal(goal_id: int):
    goals = DataInterface().load_goals(flask.g.oauth_user).goals
    goal = goals.get(goal_id)
    if goal is None:
        return _error("not_found", "Goal not found", 404)
    return flask.jsonify(goal=_serialize_goal(goal, goals))


@actions_api.route("/openapi.json")
def openapi_schema():
    config = ConfigManager()
    actions = config.gpt_actions
    read_scope = actions.read_scope
    goal_schema = {
        "type": "object",
        "properties": {
            "id": {"type": "integer"},
            "name": {"type": "string"},
            "description": {"type": "string"},
            "state": {"type": "string", "enum": list(_STATE_NAMES)},
            "creation_date": {"type": "string", "format": "date-time"},
            "completion_date": {"type": ["string", "null"], "format": "date-time"},
            "planned_completion_date": {"type": ["string", "null"], "format": "date-time"},
            "last_modified": {"type": "string", "format": "date-time"},
            "parent_id": {"type": ["integer", "null"]},
            "children": {"type": "array", "items": {"$ref": "#/components/schemas/Goal"}},
        },
        "required": ["id", "name", "description", "state", "children"],
    }
    list_response_schema = {
        "type": "object",
        "properties": {
            "goals": {"type": "array", "items": {"$ref": "#/components/schemas/Goal"}},
            "offset": {"type": "integer"},
            "limit": {"type": "integer"},
            "total": {"type": "integer"},
            "has_more": {"type": "boolean"},
        },
    }
    return flask.jsonify({
        "openapi": "3.1.0",
        "info": {"title": "Nabicat Todoist Actions", "version": "1.0.0"},
        "servers": [{"url": config.site_url.rstrip("/")}],
        "paths": {
            "/actions/todoist/goals": {"get": {
                "operationId": "listGoals",
                "summary": "List the authenticated user's goals",
                "security": [{"oauth2": [read_scope]}],
                "parameters": [
                    {"name": "state", "in": "query", "schema": {
                        "type": "string", "enum": list(_STATE_NAMES), "default": "active"}},
                    {"name": "offset", "in": "query", "schema": {"type": "integer", "minimum": 0}},
                    {"name": "limit", "in": "query", "schema": {
                        "type": "integer", "minimum": 1,
                        "maximum": actions.max_page_size}},
                ],
                "responses": {"200": {
                    "description": "A page of goals",
                    "content": {"application/json": {"schema": list_response_schema}},
                }},
            }},
            "/actions/todoist/goals/{goal_id}": {"get": {
                "operationId": "getGoal",
                "summary": "Retrieve a goal by ID",
                "security": [{"oauth2": [read_scope]}],
                "parameters": [{"name": "goal_id", "in": "path", "required": True,
                                "schema": {"type": "integer"}}],
                "responses": {
                    "200": {
                        "description": "The requested goal",
                        "content": {"application/json": {"schema": {
                            "type": "object",
                            "properties": {
                                "goal": {"$ref": "#/components/schemas/Goal"},
                            },
                        }}},
                    },
                    "404": {"description": "Goal not found"},
                },
            }},
        },
        "components": {
            "schemas": {"Goal": goal_schema},
            "securitySchemes": {"oauth2": {
                "type": "oauth2",
                "flows": {"authorizationCode": {
                    "authorizationUrl": f"{config.site_url.rstrip('/')}/oauth/authorize",
                    "tokenUrl": f"{config.site_url.rstrip('/')}/oauth/token",
                    "refreshUrl": f"{config.site_url.rstrip('/')}/oauth/token",
                    "scopes": {read_scope: "Read Todoist goals"},
                }},
            }},
        },
    })
