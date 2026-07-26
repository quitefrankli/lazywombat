from datetime import datetime
import hashlib
import json

import flask
from flask import Blueprint, request

from web_app.app import csrf
from web_app.config import ConfigManager
from web_app.oauth import bearer_user
from web_app.redis_client import get_redis
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


def _parse_json():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return None, _error("invalid_request", "A JSON object is required", 400)
    return payload, None


def _parse_date(value, field: str, nullable=False):
    if value is None and nullable:
        return None, None
    if not isinstance(value, str):
        return None, _error(
            "invalid_request", f"{field} must be an ISO 8601 date-time", 400
        )
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")), None
    except ValueError:
        return None, _error(
            "invalid_request", f"{field} must be an ISO 8601 date-time", 400
        )


def _idempotent(payload: dict, mutation):
    actions = ConfigManager().gpt_actions
    idempotency_key = request.headers.get("Idempotency-Key", "").strip()
    if not idempotency_key:
        return _error(
            "missing_idempotency_key", "Idempotency-Key header is required", 400
        )
    if len(idempotency_key) > actions.idempotency_key_max_length:
        return _error(
            "invalid_idempotency_key", "Idempotency-Key is too long", 400
        )

    fingerprint = hashlib.sha256(json.dumps(
        {"method": request.method, "path": request.path, "payload": payload},
        sort_keys=True,
        separators=(",", ":"),
    ).encode()).hexdigest()
    key_digest = hashlib.sha256(
        f"{flask.g.oauth_user.id}:{request.method}:{request.path}:{idempotency_key}".encode()
    ).hexdigest()
    redis_key = f"nabicat:todoist:idempotency:{key_digest}"
    pending = json.dumps({"fingerprint": fingerprint, "status": "pending"})
    redis = get_redis()

    if not redis.set(
        redis_key, pending, nx=True, ex=actions.idempotency_pending_ttl_s
    ):
        existing = json.loads(redis.get(redis_key))
        if existing["fingerprint"] != fingerprint:
            return _error(
                "idempotency_conflict",
                "Idempotency-Key was already used with a different request",
                409,
            )
        if existing["status"] == "pending":
            return _error(
                "request_in_progress",
                "A request with this Idempotency-Key is still in progress",
                409,
            )
        return flask.jsonify(existing["body"]), existing["status_code"]

    try:
        body, status_code = mutation()
    except Exception:
        redis.delete(redis_key)
        raise
    redis.set(redis_key, json.dumps({
        "fingerprint": fingerprint,
        "status": "complete",
        "body": body,
        "status_code": status_code,
    }), ex=actions.idempotency_ttl_s)
    return flask.jsonify(body), status_code


@actions_api.route("/todoist/goals", methods=["GET"])
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


@actions_api.route("/todoist/goals", methods=["POST"])
@bearer_user(ConfigManager().gpt_actions.read_scope)
def create_goal():
    payload, error = _parse_json()
    if error:
        return error
    allowed = {"name", "description", "parent_id", "planned_completion_date"}
    if set(payload) - allowed:
        return _error("invalid_request", "Unsupported fields were provided", 400)
    name = payload.get("name")
    if not isinstance(name, str) or not name.strip():
        return _error("invalid_request", "name must be a non-empty string", 400)
    description = payload.get("description", "")
    if not isinstance(description, str):
        return _error("invalid_request", "description must be a string", 400)
    parent_id = payload.get("parent_id")
    if parent_id is not None and (
        not isinstance(parent_id, int) or isinstance(parent_id, bool)
    ):
        return _error("invalid_request", "parent_id must be an integer or null", 400)
    planned = None
    if "planned_completion_date" in payload:
        planned, error = _parse_date(
            payload["planned_completion_date"], "planned_completion_date", nullable=True
        )
        if error:
            return error

    def mutation():
        with DataInterface().edit_goals(flask.g.oauth_user) as goals:
            if parent_id is not None and parent_id not in goals.goals:
                return {"error": "not_found", "message": "Parent goal not found"}, 404
            goal_id = 0 if not goals.goals else max(goals.goals) + 1
            goal = Goal(
                id=goal_id,
                name=name.strip(),
                description=description,
                state=GoalState.ACTIVE,
                planned_completion_date=planned,
                parent=parent_id,
            )
            goals.goals[goal_id] = goal
            if parent_id is not None:
                goals.goals[parent_id].children.append(goal_id)
            body = {"goal": _serialize_goal(goal, goals.goals)}
        return body, 201

    return _idempotent(payload, mutation)


@actions_api.route("/todoist/goals/<int:goal_id>", methods=["PATCH"])
@bearer_user(ConfigManager().gpt_actions.read_scope)
def update_goal(goal_id: int):
    payload, error = _parse_json()
    if error:
        return error
    allowed = {"name", "description", "planned_completion_date", "state"}
    if not payload or set(payload) - allowed:
        return _error("invalid_request", "No supported fields were provided", 400)
    if "name" in payload and (
        not isinstance(payload["name"], str) or not payload["name"].strip()
    ):
        return _error("invalid_request", "name must be a non-empty string", 400)
    if "description" in payload and not isinstance(payload["description"], str):
        return _error("invalid_request", "description must be a string", 400)
    state = None
    if "state" in payload:
        state_name = payload["state"]
        if not isinstance(state_name, str) or state_name.lower() not in _STATE_NAMES:
            return _error("invalid_state", "Unsupported goal state", 400)
        state = _STATE_NAMES[state_name.lower()]
    planned = None
    if "planned_completion_date" in payload:
        planned, error = _parse_date(
            payload["planned_completion_date"], "planned_completion_date", nullable=True
        )
        if error:
            return error

    def mutation():
        with DataInterface().edit_goals(flask.g.oauth_user) as goals:
            goal = goals.goals.get(goal_id)
            if goal is None:
                return {"error": "not_found", "message": "Goal not found"}, 404
            if "name" in payload:
                goal.name = payload["name"].strip()
            if "description" in payload:
                goal.description = payload["description"]
            if "planned_completion_date" in payload:
                goal.planned_completion_date = planned
            if state is not None:
                goal.state = state
                goal.completion_date = (
                    goal.completion_date or datetime.now()
                    if state == GoalState.COMPLETED else None
                )
            goal.last_modified = datetime.now()
            body = {"goal": _serialize_goal(goal, goals.goals)}
        return body, 200

    return _idempotent(payload, mutation)


@actions_api.route("/todoist/goals/<int:goal_id>/complete", methods=["POST"])
@bearer_user(ConfigManager().gpt_actions.read_scope)
def complete_goal(goal_id: int):
    payload = {}

    def mutation():
        with DataInterface().edit_goals(flask.g.oauth_user) as goals:
            goal = goals.goals.get(goal_id)
            if goal is None:
                return {"error": "not_found", "message": "Goal not found"}, 404
            if goal.state != GoalState.COMPLETED:
                goal.state = GoalState.COMPLETED
                goal.completion_date = datetime.now()
                goal.last_modified = datetime.now()
            body = {"goal": _serialize_goal(goal, goals.goals)}
        return body, 200

    return _idempotent(payload, mutation)


@actions_api.route("/todoist/goals/<int:goal_id>/log", methods=["POST"])
@bearer_user(ConfigManager().gpt_actions.read_scope)
def log_goal(goal_id: int):
    payload, error = _parse_json()
    if error:
        return error
    if set(payload) != {"log"}:
        return _error("invalid_request", "Only the log field is supported", 400)
    log = payload.get("log")
    if not isinstance(log, str) or not log.strip():
        return _error("invalid_request", "log must be a non-empty string", 400)

    def mutation():
        with DataInterface().edit_goals(flask.g.oauth_user) as goals:
            goal = goals.goals.get(goal_id)
            if goal is None:
                return {"error": "not_found", "message": "Goal not found"}, 404
            date = datetime.now().strftime("%d/%m/%Y")
            goal.description += f"\n\n{'-' * 10}\n{date}\n{log.strip()}\n{'-' * 10}"
            goal.last_modified = datetime.now()
            body = {"goal": _serialize_goal(goal, goals.goals)}
        return body, 200

    return _idempotent(payload, mutation)


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
    child_goal_schema = {
        "type": "object",
        "description": "A nested goal. Its children use the same goal shape.",
        "properties": {
            "id": {"type": "integer"},
            "name": {"type": "string"},
            "description": {"type": "string"},
            "state": {"type": "string", "enum": list(_STATE_NAMES)},
            "parent_id": {"type": ["integer", "null"]},
            "children": {"type": "array", "items": {"type": "object"}},
        },
        "required": ["id", "name", "description", "state", "children"],
    }
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
            "children": {"type": "array", "items": child_goal_schema},
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
    idempotency_parameter = {
        "name": "Idempotency-Key",
        "in": "header",
        "required": True,
        "description": "Unique key used to safely retry this write request.",
        "schema": {
            "type": "string",
            "maxLength": actions.idempotency_key_max_length,
        },
    }
    goal_response = {
        "description": "The updated goal",
        "content": {"application/json": {"schema": {
            "type": "object",
            "properties": {"goal": {"$ref": "#/components/schemas/Goal"}},
            "required": ["goal"],
        }}},
    }
    return flask.jsonify({
        "openapi": "3.1.0",
        "info": {
            "title": "Nabicat Todoist Actions",
            "version": "1.0.0",
            "x-privacyPolicyUrl": f"{config.site_url.rstrip('/')}/privacy",
        },
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
            }, "post": {
                "operationId": "createGoal",
                "summary": "Create a goal",
                "security": [{"oauth2": [read_scope]}],
                "parameters": [idempotency_parameter],
                "requestBody": {
                    "required": True,
                    "content": {"application/json": {"schema": {
                        "$ref": "#/components/schemas/CreateGoalRequest",
                    }}},
                },
                "responses": {
                    "201": goal_response,
                    "400": {"description": "Invalid request"},
                    "404": {"description": "Parent goal not found"},
                    "409": {"description": "Idempotency conflict"},
                },
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
            }, "patch": {
                "operationId": "updateGoal",
                "summary": "Update selected fields and explicitly set goal state",
                "security": [{"oauth2": [read_scope]}],
                "parameters": [
                    {"name": "goal_id", "in": "path", "required": True,
                     "schema": {"type": "integer"}},
                    idempotency_parameter,
                ],
                "requestBody": {
                    "required": True,
                    "content": {"application/json": {"schema": {
                        "$ref": "#/components/schemas/UpdateGoalRequest",
                    }}},
                },
                "responses": {
                    "200": goal_response,
                    "400": {"description": "Invalid request"},
                    "404": {"description": "Goal not found"},
                    "409": {"description": "Idempotency conflict"},
                },
            }},
            "/actions/todoist/goals/{goal_id}/complete": {"post": {
                "operationId": "completeGoal",
                "summary": "Set a goal's state to completed",
                "security": [{"oauth2": [read_scope]}],
                "parameters": [
                    {"name": "goal_id", "in": "path", "required": True,
                     "schema": {"type": "integer"}},
                    idempotency_parameter,
                ],
                "responses": {
                    "200": goal_response,
                    "404": {"description": "Goal not found"},
                    "409": {"description": "Idempotency conflict"},
                },
            }},
            "/actions/todoist/goals/{goal_id}/log": {"post": {
                "operationId": "logGoalProgress",
                "summary": "Append a dated progress note to a goal",
                "security": [{"oauth2": [read_scope]}],
                "parameters": [
                    {"name": "goal_id", "in": "path", "required": True,
                     "schema": {"type": "integer"}},
                    idempotency_parameter,
                ],
                "requestBody": {
                    "required": True,
                    "content": {"application/json": {"schema": {
                        "$ref": "#/components/schemas/GoalLogRequest",
                    }}},
                },
                "responses": {
                    "200": goal_response,
                    "400": {"description": "Invalid request"},
                    "404": {"description": "Goal not found"},
                    "409": {"description": "Idempotency conflict"},
                },
            }},
        },
        "components": {
            "schemas": {
                "Goal": goal_schema,
                "CreateGoalRequest": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "minLength": 1},
                        "description": {"type": "string"},
                        "parent_id": {"type": ["integer", "null"]},
                        "planned_completion_date": {
                            "type": ["string", "null"], "format": "date-time",
                        },
                    },
                    "required": ["name"],
                    "additionalProperties": False,
                },
                "UpdateGoalRequest": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "minLength": 1},
                        "description": {"type": "string"},
                        "planned_completion_date": {
                            "type": ["string", "null"], "format": "date-time",
                        },
                        "state": {"type": "string", "enum": list(_STATE_NAMES)},
                    },
                    "minProperties": 1,
                    "additionalProperties": False,
                },
                "GoalLogRequest": {
                    "type": "object",
                    "properties": {"log": {"type": "string", "minLength": 1}},
                    "required": ["log"],
                    "additionalProperties": False,
                },
            },
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
