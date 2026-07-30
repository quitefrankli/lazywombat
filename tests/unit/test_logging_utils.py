import json
import logging

from flask import g

import web_app.__main__  # noqa: F401
from web_app.logging_utils import log_event


def _event_records(caplog, event: str) -> list[dict]:
    records = []
    for record in caplog.records:
        try:
            payload = json.loads(record.getMessage())
        except json.JSONDecodeError:
            continue
        if payload.get("event") == event:
            records.append(payload)
    return records


def test_log_event_has_consistent_context_fields(app, caplog):
    with app.test_request_context(
        "/example",
        environ_base={"REMOTE_ADDR": "127.0.0.1"},
    ), caplog.at_level(logging.INFO):
        g.request_id = "request-123"
        log_event(
            "metrics",
            "metric.created",
            user="alice",
            metric_id=7,
        )

    assert _event_records(caplog, "metric.created") == [{
        "app": "metrics",
        "event": "metric.created",
        "ip": "127.0.0.1",
        "metric_id": 7,
        "request_id": "request-123",
        "user": "alice",
    }]


def test_request_lifecycle_uses_one_correlation_id(client, caplog):
    from web_app.config import ConfigManager

    with caplog.at_level(logging.INFO):
        response = client.get("/privacy")

    started = _event_records(caplog, "request.started")
    completed = _event_records(caplog, "request.completed")

    assert response.status_code == 200
    assert len(started) == 1
    assert len(completed) == 1
    request_id = response.headers[ConfigManager().request_id_header]
    assert started[0]["request_id"] == request_id
    assert completed[0]["request_id"] == request_id
    assert completed[0]["status"] == 200
    assert completed[0]["duration_ms"] >= 0
    assert completed[0]["app"] == "web"
    assert completed[0]["ip"] == "127.0.0.1"
    assert completed[0]["user"] is None
