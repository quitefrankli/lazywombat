import base64
from contextlib import contextmanager
from datetime import datetime
import json
import logging
from urllib.parse import quote_plus
from unittest.mock import patch

import flask_login
import pytest

import web_app.__main__
from web_app.config import ConfigManager
from web_app.redis_client import get_redis
from web_app.todoist.data_interface import Goal, Goals, GoalState
from web_app.users import User, UsersFile


@pytest.fixture
def oauth_config(monkeypatch, oauth_user):
    config = ConfigManager()
    monkeypatch.setenv("OAUTH_CLIENT_ID", "chatgpt")
    monkeypatch.setenv("OAUTH_CLIENT_SECRET", "secret")
    monkeypatch.setenv("OAUTH_REDIRECT_URIS", "https://chat.openai.com/aip/callback")
    users = UsersFile(root=[oauth_user])
    monkeypatch.setattr(
        "web_app.helpers.login_manager._user_callback",
        lambda username: users.get(username),
    )
    monkeypatch.setattr(
        "web_app.oauth.DataInterface.load_users",
        lambda self: users,
    )
    get_redis().flushdb()
    return config


@pytest.fixture
def oauth_user():
    return User("alice", "password", "alice-folder")


def _login(client, user):
    with client.session_transaction() as session:
        session["_user_id"] = user.id
        session["_fresh"] = True


def _authorize(client, user, **overrides):
    params = {
        "response_type": "code",
        "client_id": "chatgpt",
        "redirect_uri": "https://chat.openai.com/aip/callback",
        "scope": "todoist.goals.read",
        "state": "opaque-state",
    }
    params.update(overrides)
    _login(client, user)
    get_response = client.get("/oauth/authorize", query_string=params)
    assert get_response.status_code == 200
    return client.post("/oauth/authorize", data={**params, "decision": "approve"})


def _exchange(client, code, redirect_uri="https://chat.openai.com/aip/callback"):
    auth = base64.b64encode(b"chatgpt:secret").decode()
    return client.post(
        "/oauth/token",
        headers={"Authorization": f"Basic {auth}"},
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
        },
    )


def test_authorize_rejects_unregistered_redirect(client, oauth_config):
    response = client.get(
        "/oauth/authorize",
        query_string={
            "response_type": "code",
            "client_id": "chatgpt",
            "redirect_uri": "https://evil.example/callback",
            "scope": "todoist.goals.read",
        },
    )
    assert response.status_code == 400
    assert response.get_json()["error"] == "invalid_request"


def test_authorize_requires_login_and_preserves_query(client, oauth_config):
    response = client.get(
        "/oauth/authorize",
        query_string={
            "response_type": "code",
            "client_id": "chatgpt",
            "redirect_uri": "https://chat.openai.com/aip/callback",
            "scope": "todoist.goals.read",
            "state": "keep-me",
        },
    )
    assert response.status_code == 302
    assert "/account/login?next=" in response.location
    assert "keep-me" in response.location


def test_consent_denial_round_trips_state(client, oauth_config, oauth_user):
    _login(client, oauth_user)
    response = client.post(
        "/oauth/authorize",
        data={
            "response_type": "code",
            "client_id": "chatgpt",
            "redirect_uri": "https://chat.openai.com/aip/callback",
            "scope": "todoist.goals.read",
            "state": "state-1",
            "decision": "deny",
        },
    )
    assert response.status_code == 302
    assert "error=access_denied" in response.location
    assert "state=state-1" in response.location


def test_code_is_one_time_and_redirect_bound(client, oauth_config, oauth_user):
    response = _authorize(client, oauth_user)
    assert "state=opaque-state" in response.location
    code = response.location.split("code=", 1)[1].split("&", 1)[0]

    wrong_redirect = _exchange(client, code, "https://chat.openai.com/aip/other")
    assert wrong_redirect.status_code == 400

    token_response = _exchange(client, code)
    assert token_response.status_code == 200
    payload = token_response.get_json()
    assert payload["scope"] == "todoist.goals.read"
    assert payload["token_type"] == "Bearer"

    reused = _exchange(client, code)
    assert reused.status_code == 400
    assert reused.get_json()["error"] == "invalid_grant"


def test_token_accepts_form_encoded_basic_client_secret(
        client, oauth_config, oauth_user, monkeypatch):
    secret = "secret:/+ with%symbols"
    monkeypatch.setenv("OAUTH_CLIENT_SECRET", secret)
    response = _authorize(client, oauth_user)
    code = response.location.split("code=", 1)[1].split("&", 1)[0]
    credentials = f"{quote_plus('chatgpt')}:{quote_plus(secret)}".encode()
    auth = base64.b64encode(credentials).decode()

    token_response = client.post(
        "/oauth/token",
        headers={"Authorization": f"Basic {auth}"},
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": "https://chat.openai.com/aip/callback",
        },
    )

    assert token_response.status_code == 200


def test_invalid_client_logs_safe_diagnostics(
        client, oauth_config, caplog):
    supplied_secret = "do-not-log-this-secret"
    auth = base64.b64encode(
        f"chatgpt:{supplied_secret}".encode()
    ).decode()

    with caplog.at_level(logging.WARNING, logger="web_app.oauth"):
        response = client.post(
            "/oauth/token",
            headers={"Authorization": f"Basic {auth}"},
            data={"grant_type": "authorization_code", "code": "do-not-log-code"},
        )

    assert response.status_code == 401
    event = next(
        json.loads(record.getMessage())
        for record in caplog.records
        if record.getMessage().startswith("{")
        and json.loads(record.getMessage()).get("event")
        == "oauth.client_authentication_failed"
    )
    assert event["method"] == "basic"
    assert event["reason"] == "secret_mismatch"
    assert event["client_id_match"] is True
    assert supplied_secret not in caplog.text
    assert "do-not-log-code" not in caplog.text


def test_refresh_rotates_and_revoke_invalidates(client, oauth_config, oauth_user):
    response = _authorize(client, oauth_user)
    code = response.location.split("code=", 1)[1].split("&", 1)[0]
    tokens = _exchange(client, code).get_json()
    auth = base64.b64encode(b"chatgpt:secret").decode()

    refreshed = client.post(
        "/oauth/token",
        headers={"Authorization": f"Basic {auth}"},
        data={"grant_type": "refresh_token", "refresh_token": tokens["refresh_token"]},
    )
    assert refreshed.status_code == 200
    assert refreshed.get_json()["refresh_token"] != tokens["refresh_token"]

    replay = client.post(
        "/oauth/token",
        headers={"Authorization": f"Basic {auth}"},
        data={"grant_type": "refresh_token", "refresh_token": tokens["refresh_token"]},
    )
    assert replay.status_code == 400

    access_token = refreshed.get_json()["access_token"]
    revoked = client.post(
        "/oauth/revoke",
        headers={"Authorization": f"Basic {auth}"},
        data={"token": access_token},
    )
    assert revoked.status_code == 200
    assert client.get(
        "/actions/todoist/goals",
        headers={"Authorization": f"Bearer {access_token}"},
    ).status_code == 401


def test_actions_list_active_tree_and_get_goal(client, oauth_config, oauth_user):
    response = _authorize(client, oauth_user)
    code = response.location.split("code=", 1)[1].split("&", 1)[0]
    access_token = _exchange(client, code).get_json()["access_token"]
    goals = Goals(goals={
        1: Goal(id=1, name="Root", state=GoalState.ACTIVE, children=[2],
                creation_date=datetime(2026, 1, 1), last_modified=datetime(2026, 1, 2)),
        2: Goal(id=2, name="Child", state=GoalState.COMPLETED, parent=1,
                creation_date=datetime(2026, 1, 1), last_modified=datetime(2026, 1, 3)),
        3: Goal(id=3, name="Backlog", state=GoalState.BACKLOGGED,
                creation_date=datetime(2026, 1, 1), last_modified=datetime(2026, 1, 4)),
    })
    headers = {"Authorization": f"Bearer {access_token}"}

    with patch("web_app.todoist.api.DataInterface") as data_interface:
        data_interface.return_value.load_goals.return_value = goals
        listed = client.get("/actions/todoist/goals", headers=headers)
        detail = client.get("/actions/todoist/goals/2", headers=headers)
        filtered = client.get("/actions/todoist/goals?state=backlogged", headers=headers)

    assert listed.status_code == 200
    assert [goal["id"] for goal in listed.get_json()["goals"]] == [1]
    assert listed.get_json()["goals"][0]["children"][0]["id"] == 2
    assert detail.get_json()["goal"]["state"] == "completed"
    assert filtered.get_json()["goals"][0]["id"] == 3


def test_actions_errors_and_openapi(client, oauth_config):
    assert client.get("/actions/todoist/goals").status_code == 401
    schema = client.get("/actions/openapi.json")
    assert schema.status_code == 200
    document = schema.get_json()
    assert document["openapi"].startswith("3.")
    assert document["info"]["x-privacyPolicyUrl"] == "https://nabicat.site/privacy"
    assert document["paths"]["/actions/todoist/goals"]["get"]["operationId"] == "listGoals"
    assert document["paths"]["/actions/todoist/goals"]["post"]["operationId"] == "createGoal"
    assert document["paths"]["/actions/todoist/goals/{goal_id}"]["patch"]["operationId"] == "updateGoal"
    assert document["paths"]["/actions/todoist/goals/{goal_id}/complete"]["post"][
        "operationId"
    ] == "completeGoal"
    assert document["paths"]["/actions/todoist/goals/{goal_id}/log"]["post"][
        "operationId"
    ] == "logGoalProgress"
    goal_children = document["components"]["schemas"]["Goal"]["properties"]["children"]
    assert "$ref" not in goal_children["items"]


def test_privacy_policy_is_public(client, oauth_config):
    response = client.get("/privacy")

    assert response.status_code == 200
    assert b"Privacy Policy" in response.data


def test_actions_validate_filters_pagination_scope_and_missing_goal(
        client, oauth_config, oauth_user):
    response = _authorize(client, oauth_user)
    code = response.location.split("code=", 1)[1].split("&", 1)[0]
    access_token = _exchange(client, code).get_json()["access_token"]
    headers = {"Authorization": f"Bearer {access_token}"}

    with patch("web_app.todoist.api.DataInterface") as data_interface:
        data_interface.return_value.load_goals.return_value = Goals(goals={})
        assert client.get(
            "/actions/todoist/goals?state=unknown", headers=headers
        ).status_code == 400
        assert client.get(
            "/actions/todoist/goals?limit=101", headers=headers
        ).status_code == 400
        assert client.get(
            "/actions/todoist/goals/999", headers=headers
        ).status_code == 404

    import hashlib
    import json
    digest = hashlib.sha256(b"wrong-scope").hexdigest()
    get_redis().set(
        "nabicat:oauth:access:" + digest,
        json.dumps({
            "username": oauth_user.id,
            "client_id": "chatgpt",
            "scope": "other.scope",
        }),
        ex=60,
    )
    forbidden = client.get(
        "/actions/todoist/goals",
        headers={"Authorization": "Bearer wrong-scope"},
    )
    assert forbidden.status_code == 403


def test_actions_create_update_complete_and_log_are_idempotent(
        client, oauth_config, oauth_user):
    response = _authorize(client, oauth_user)
    code = response.location.split("code=", 1)[1].split("&", 1)[0]
    access_token = _exchange(client, code).get_json()["access_token"]
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Idempotency-Key": "request-1",
    }
    stored = Goals(goals={})

    @contextmanager
    def edit_goals(_user):
        yield stored

    with patch("web_app.todoist.api.DataInterface") as data_interface:
        data_interface.return_value.edit_goals.side_effect = edit_goals
        created = client.post(
            "/actions/todoist/goals",
            headers=headers,
            json={
                "name": "Ship API",
                "description": "Initial scope",
                "planned_completion_date": "2026-08-01T09:30:00",
            },
        )
        replayed = client.post(
            "/actions/todoist/goals",
            headers=headers,
            json={
                "name": "Ship API",
                "description": "Initial scope",
                "planned_completion_date": "2026-08-01T09:30:00",
            },
        )
        headers["Idempotency-Key"] = "request-2"
        updated = client.patch(
            "/actions/todoist/goals/0",
            headers=headers,
            json={"name": "Ship actions API", "state": "backlogged"},
        )
        headers["Idempotency-Key"] = "request-3"
        completed = client.post(
            "/actions/todoist/goals/0/complete", headers=headers
        )
        headers["Idempotency-Key"] = "request-4"
        logged = client.post(
            "/actions/todoist/goals/0/log",
            headers=headers,
            json={"log": "Implemented the write endpoints"},
        )

    assert created.status_code == 201
    assert created.get_json()["goal"]["name"] == "Ship API"
    assert replayed.status_code == 201
    assert len(stored.goals) == 1
    assert updated.get_json()["goal"]["state"] == "backlogged"
    assert completed.get_json()["goal"]["state"] == "completed"
    assert logged.get_json()["goal"]["description"].endswith(
        "Implemented the write endpoints\n----------"
    )


def test_action_writes_return_structured_validation_and_idempotency_errors(
        client, oauth_config, oauth_user):
    response = _authorize(client, oauth_user)
    code = response.location.split("code=", 1)[1].split("&", 1)[0]
    access_token = _exchange(client, code).get_json()["access_token"]
    headers = {"Authorization": f"Bearer {access_token}"}

    missing_key = client.post(
        "/actions/todoist/goals", headers=headers, json={"name": "Goal"}
    )
    headers["Idempotency-Key"] = "same-key"
    invalid = client.post(
        "/actions/todoist/goals", headers=headers, json={"name": ""}
    )

    stored = Goals(goals={})

    @contextmanager
    def edit_goals(_user):
        yield stored

    with patch("web_app.todoist.api.DataInterface") as data_interface:
        data_interface.return_value.edit_goals.side_effect = edit_goals
        first = client.post(
            "/actions/todoist/goals", headers=headers, json={"name": "One"}
        )
        conflict = client.post(
            "/actions/todoist/goals", headers=headers, json={"name": "Two"}
        )
        headers["Idempotency-Key"] = "missing-goal"
        missing = client.patch(
            "/actions/todoist/goals/999", headers=headers, json={"name": "Nope"}
        )

    assert missing_key.status_code == 400
    assert missing_key.get_json()["error"] == "missing_idempotency_key"
    assert invalid.status_code == 400
    assert invalid.get_json()["error"] == "invalid_request"
    assert first.status_code == 201
    assert conflict.status_code == 409
    assert conflict.get_json()["error"] == "idempotency_conflict"
    assert missing.status_code == 404
    assert missing.get_json()["error"] == "not_found"


def test_account_revocation_removes_all_user_tokens(client, oauth_config, oauth_user):
    response = _authorize(client, oauth_user)
    code = response.location.split("code=", 1)[1].split("&", 1)[0]
    access_token = _exchange(client, code).get_json()["access_token"]
    _login(client, oauth_user)

    with patch("web_app.helpers.DataInterface.load_users",
               return_value=UsersFile(root=[oauth_user])):
        response = client.post("/account/oauth/revoke")
    assert response.status_code == 302
    assert client.get(
        "/actions/todoist/goals",
        headers={"Authorization": f"Bearer {access_token}"},
    ).status_code == 401
