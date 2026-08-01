import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from web_app.config import ConfigManager
from web_app.sentinel.models import (
    ExecutionStatus,
    Finding,
    Report,
    RunStatus,
    RunVerdict,
)
from web_app.sentinel.qa_cli import _error_payload, _result_payload
from web_app.sentinel.runner import (
    _PersistedRunTerminal,
    _register_network_findings,
    _verdict_prompt,
    create_run,
    execute_run,
)
from web_app.sentinel.target_policy import ValidatedTarget


def _report(**overrides) -> Report:
    now = datetime.now(timezone.utc).isoformat()
    values = {
        "run_id": "a" * 32,
        "target_url": "http://127.0.0.1:12345/feature",
        "target_hostname": "127.0.0.1",
        "prompt": "Check the feature.",
        "limit_s": 60,
        "created_at": now,
        "updated_at": now,
    }
    values.update(overrides)
    return Report.model_validate(values)


@pytest.fixture(autouse=True)
def restore_runtime_config():
    config = ConfigManager()
    original = (
        config.debug_mode,
        config.production_data_root,
        config.sentinel.allow_local_targets,
    )
    yield
    (
        config.debug_mode,
        config.production_data_root,
        config.sentinel.allow_local_targets,
    ) = original


def test_report_schema_and_cli_evidence_paths_are_versioned_and_path_safe(tmp_path):
    config = ConfigManager()
    config.debug_mode = False
    config.production_data_root = tmp_path / "normal-data"
    report = _report(
        screenshots=[
            "screenshots/step-00.png",
            "screenshots/../../outside.png",
            str(tmp_path / "secret.png"),
        ],
        annotated_screenshots=["screenshots/step-00-annot.png"],
    )

    payload = _result_payload(
        report,
        git_revision=None,
        working_tree_dirty=None,
        report_url_base="",
    )

    run_dir = tmp_path / "normal-data" / "sentinel" / "runs" / report.run_id
    assert report.schema_version == config.sentinel.report_schema_version
    assert report.model_dump(mode="json")["schema_version"] == (
        config.sentinel.report_schema_version
    )
    assert payload["evidence_paths"] == [
        str((run_dir / "screenshots/step-00.png").resolve()),
        str((run_dir / "screenshots/step-00-annot.png").resolve()),
    ]
    assert _error_payload("failed")["evidence_paths"] == []


def test_network_findings_require_the_exact_target_origin():
    class FakePage:
        def __init__(self):
            self.handlers = {}

        def on(self, event, callback):
            self.handlers[event] = callback

    def failed_request(url):
        return SimpleNamespace(
            url=url,
            method="GET",
            failure="net::ERR_CONNECTION_RESET",
        )

    page = FakePage()
    report = _report()
    _register_network_findings(page, report)

    page.handlers["requestfailed"](
        failed_request("http://127.0.0.1:12345/api/save?token=secret")
    )
    page.handlers["requestfailed"](
        failed_request("http://127.0.0.1:12346/api/report")
    )
    page.handlers["requestfailed"](
        failed_request("https://127.0.0.1:12345/api/save")
    )

    assert len(report.findings) == 1
    assert report.findings[0].url == "http://127.0.0.1:12345/api/save"


def test_console_findings_remain_persisted_but_are_excluded_from_verdict_prompt():
    report = _report(
        findings=[
            Finding(severity="info", title="Console", detail="third-party noise"),
            Finding(
                severity="error",
                title="First-party server error",
                detail="GET /api returned 500",
                kind="network.http_5xx",
            ),
        ]
    )

    prompt = json.loads(_verdict_prompt(report))

    assert len(report.findings) == 2
    assert [finding["kind"] for finding in prompt["findings"]] == [
        "network.http_5xx"
    ]


def test_create_run_arms_redis_before_persisting_and_does_not_persist_on_failure():
    target = ValidatedTarget("https://example.com/", "example.com")
    redis = Mock()
    call_order = []
    redis.set.side_effect = lambda *_args, **_kwargs: call_order.append("redis")

    with (
        patch("web_app.sentinel.runner.get_redis", return_value=redis),
        patch(
            "web_app.sentinel.runner._save",
            side_effect=lambda _report: call_order.append("persist"),
        ),
    ):
        create_run(target, "Check it", 60)

    assert call_order == ["redis", "persist"]

    redis.set.side_effect = RuntimeError("redis unavailable")
    with (
        patch("web_app.sentinel.runner.get_redis", return_value=redis),
        patch("web_app.sentinel.runner._save") as save,
        pytest.raises(RuntimeError, match="redis unavailable"),
    ):
        create_run(target, "Check it", 60)
    save.assert_not_called()


def test_create_run_terminalizes_a_report_persisted_before_interrupt():
    target = ValidatedTarget("https://example.com/", "example.com")
    redis = Mock()
    persisted = {}

    class FakeDataInterface:
        def load_report(self, run_id):
            return persisted.get(run_id)

    def interrupted_save(report):
        persisted[report.run_id] = report.model_copy(deep=True)
        if report.lifecycle == ExecutionStatus.QUEUED:
            raise KeyboardInterrupt

    with (
        patch("web_app.sentinel.runner.get_redis", return_value=redis),
        patch("web_app.sentinel.runner.DataInterface", FakeDataInterface),
        patch("web_app.sentinel.runner._save", side_effect=interrupted_save),
        pytest.raises(KeyboardInterrupt),
    ):
        create_run(target, "Check it", 60)

    terminal = next(iter(persisted.values()))
    assert terminal.lifecycle == ExecutionStatus.INTERRUPTED
    assert terminal.status == RunStatus.FAILED
    assert terminal.finished_at is not None
    redis.delete.assert_called_once()


def test_execute_run_final_save_race_adopts_terminal_report_and_still_cleans_up():
    report = _report()
    recovered = _report(
        lifecycle=ExecutionStatus.ABANDONED,
        status=RunStatus.FAILED,
        verdict=RunVerdict.INCONCLUSIVE,
        verdict_reason_code="abandoned",
        finished_at=datetime.now(timezone.utc).isoformat(),
    )
    redis = Mock()
    data = Mock()
    saves = 0

    def race_on_final_save(_report):
        nonlocal saves
        saves += 1
        if saves == 2:
            raise _PersistedRunTerminal(recovered)

    with (
        patch("web_app.sentinel.runner._save", side_effect=race_on_final_save),
        patch(
            "web_app.sentinel.runner._execute_browser_run",
            side_effect=RuntimeError("browser failed"),
        ),
        patch("web_app.sentinel.runner._generate_title", return_value="QA"),
        patch("web_app.sentinel.runner.get_redis", return_value=redis),
        patch("web_app.sentinel.runner.DataInterface", return_value=data),
        patch("web_app.sentinel.runner.log_event") as log_event,
    ):
        result = execute_run(report)

    assert result.lifecycle == ExecutionStatus.ABANDONED
    redis.delete.assert_called_once()
    data.prune_reports.assert_called_once_with()
    assert any(
        call.args[1] == "sentinel.run_finished"
        for call in log_event.call_args_list
    )
