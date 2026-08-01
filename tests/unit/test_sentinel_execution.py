from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from web_app.config import ConfigManager
from web_app.sentinel.data_interface import DataInterface
from web_app.sentinel.models import ExecutionStatus, Report, RunVerdict
from web_app.sentinel.runner import (
    _classify_run_verdict,
    _register_network_findings,
    create_run,
    execute_run,
    get_run,
    start_run,
)
from web_app.sentinel.target_policy import ValidatedTarget


def _report(**overrides) -> Report:
    values = {
        "run_id": "a" * 32,
        "target_url": "https://example.com/",
        "target_hostname": "example.com",
        "prompt": "Check the landing page.",
        "limit_s": 60,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    values.update(overrides)
    return Report.model_validate(values)


def test_legacy_report_status_derives_truthful_lifecycle_and_verdict():
    passed = _report(status="completed")
    product_failure = _report(status="failed", verdict_reason="Checkout was blocked.")
    execution_failure = _report(status="failed", error="Browser crashed")

    assert (passed.lifecycle, passed.verdict) == (
        ExecutionStatus.FINISHED,
        RunVerdict.INCONCLUSIVE,
    )
    assert passed.verdict_reason_code == "legacy_unclassified"
    assert (product_failure.lifecycle, product_failure.verdict) == (
        ExecutionStatus.FINISHED,
        RunVerdict.FAIL,
    )
    assert (execution_failure.lifecycle, execution_failure.verdict) == (
        ExecutionStatus.EXECUTION_ERROR,
        RunVerdict.INCONCLUSIVE,
    )
    assert passed.model_dump(mode="json")["status"] == "completed"


def test_execute_run_returns_finished_pass_without_starting_a_thread():
    report = _report()
    passing_provider = type(
        "Provider",
        (),
        {"verdict_text": lambda self, _: '{"verdict":"pass","reason":"Landing page worked."}'},
    )()

    with patch("web_app.sentinel.runner._save"), patch(
        "web_app.sentinel.runner._execute_browser_run"
    ), patch("web_app.sentinel.runner._add_final_report"), patch(
        "web_app.sentinel.runner._get_provider", return_value=passing_provider
    ), patch("web_app.sentinel.runner._generate_title", return_value="Landing QA"), patch(
        "web_app.sentinel.runner.get_redis"
    ), patch("web_app.sentinel.runner.DataInterface"):
        result = execute_run(report)

    assert result is report
    assert result.lifecycle == ExecutionStatus.FINISHED
    assert result.verdict == RunVerdict.PASS
    assert result.verdict_reason_code == "qa_pass"
    assert result.status == "completed"
    assert result.finished_at is not None


def test_execute_run_records_browser_failure_as_execution_error():
    report = _report()

    with patch("web_app.sentinel.runner._save"), patch(
        "web_app.sentinel.runner._execute_browser_run", side_effect=RuntimeError("browser crashed")
    ), patch("web_app.sentinel.runner._generate_title", return_value="Landing QA"), patch(
        "web_app.sentinel.runner.get_redis"
    ), patch("web_app.sentinel.runner.DataInterface"):
        execute_run(report)

    assert report.lifecycle == ExecutionStatus.EXECUTION_ERROR
    assert report.verdict == RunVerdict.INCONCLUSIVE
    assert report.verdict_reason_code == "execution_error"
    assert report.status == "failed"
    assert report.error == "browser crashed"


def test_create_run_is_foreground_ready_and_start_run_remains_async():
    target = ValidatedTarget("https://example.com/", "example.com")
    with patch("web_app.sentinel.runner._save"), patch(
        "web_app.sentinel.runner.get_redis"
    ), patch("web_app.sentinel.runner.threading.Thread") as thread:
        report = create_run(target, "Check the page.", 60)
        thread.assert_not_called()

        payload = start_run(target, "Check the page.", 60)

    assert report.lifecycle == ExecutionStatus.QUEUED
    assert payload["status"] == "queued"
    thread.assert_called_once()
    thread.return_value.start.assert_called_once_with()


def test_execute_run_persists_interruption_as_cancelled_then_reraises():
    report = _report()
    with patch("web_app.sentinel.runner._save"), patch(
        "web_app.sentinel.runner._execute_browser_run", side_effect=KeyboardInterrupt
    ), patch("web_app.sentinel.runner._generate_title", return_value="Landing QA"), patch(
        "web_app.sentinel.runner.get_redis"
    ), patch("web_app.sentinel.runner.DataInterface"):
        try:
            execute_run(report)
        except KeyboardInterrupt:
            pass
        else:
            raise AssertionError("KeyboardInterrupt was not re-raised")

    assert report.lifecycle == ExecutionStatus.INTERRUPTED
    assert report.status == "failed"
    assert report.verdict == RunVerdict.INCONCLUSIVE
    assert report.verdict_reason_code == "interrupted"
    assert report.finished_at is not None


def test_verdict_provider_failure_and_malformed_output_are_inconclusive():
    report = _report()
    raising_provider = type(
        "Provider",
        (),
        {"verdict_text": lambda self, _: (_ for _ in ()).throw(RuntimeError("provider down"))},
    )()
    with patch("web_app.sentinel.runner._get_provider", return_value=raising_provider):
        _classify_run_verdict(report)
    assert report.verdict == RunVerdict.INCONCLUSIVE
    assert report.verdict_reason_code == "verdict_provider_error"

    malformed_provider = type("Provider", (), {"verdict_text": lambda self, _: "not JSON"})()
    with patch("web_app.sentinel.runner._get_provider", return_value=malformed_provider):
        _classify_run_verdict(report)
    assert report.verdict == RunVerdict.INCONCLUSIVE
    assert report.verdict_reason_code == "invalid_verdict_response"


def test_loading_a_stale_active_report_recovers_it_as_abandoned(tmp_path):
    data = DataInterface()
    data.sentinel_dir = tmp_path / "sentinel"
    data.runs_dir = data.sentinel_dir / "runs"
    stale_time = datetime.now(timezone.utc) - timedelta(hours=1)
    report = _report(
        lifecycle="running",
        status="running",
        updated_at=stale_time.isoformat(),
    )
    data._save_report(report, touch_updated_at=False)

    with patch.object(ConfigManager().sentinel, "abandoned_run_timeout_s", 60):
        recovered = data.load_report(report.run_id)

    assert recovered is not None
    assert recovered.lifecycle == ExecutionStatus.ABANDONED
    assert recovered.verdict == RunVerdict.INCONCLUSIVE
    assert recovered.verdict_reason_code == "abandoned"
    assert recovered.finished_at is not None
    assert recovered.status == "failed"


def test_active_snapshot_cannot_overwrite_persisted_terminal_report(tmp_path):
    data = DataInterface()
    data.sentinel_dir = tmp_path / "sentinel"
    data.runs_dir = data.sentinel_dir / "runs"
    terminal = _report(
        lifecycle="abandoned",
        verdict="inconclusive",
        status="failed",
        verdict_reason_code="abandoned",
    )
    data._save_report(terminal)
    stale_active = _report(lifecycle="running", status="running")

    result = data._save_report(stale_active)

    assert result.lifecycle == ExecutionStatus.ABANDONED
    assert data.load_report(terminal.run_id).lifecycle == ExecutionStatus.ABANDONED


def test_get_run_reconciles_active_cache_with_cross_process_terminal_state():
    import web_app.sentinel.runner as runner_module

    cached = _report(lifecycle="running", status="running")
    recovered = _report(
        lifecycle="abandoned",
        status="failed",
        verdict="inconclusive",
        verdict_reason_code="abandoned",
    )
    fake_data = type("Data", (), {"load_report": lambda self, _: recovered})()

    with patch.dict(runner_module._active_runs, {cached.run_id: cached}, clear=True), patch(
        "web_app.sentinel.runner.DataInterface", return_value=fake_data
    ):
        result = get_run(cached.run_id)

    assert result.lifecycle == ExecutionStatus.ABANDONED


def test_pruning_never_deletes_active_reports(tmp_path):
    data = DataInterface()
    data.sentinel_dir = tmp_path / "sentinel"
    data.runs_dir = data.sentinel_dir / "runs"
    active = _report(run_id="a" * 32, lifecycle="running", status="running")
    terminal = _report(
        run_id="b" * 32,
        lifecycle="finished",
        verdict="pass",
        status="completed",
    )
    data._save_report(active)
    data._save_report(terminal)

    with patch.object(ConfigManager().sentinel, "max_retained_runs", 0):
        data.prune_reports()

    assert data.run_dir(active.run_id).exists()
    assert not data.run_dir(terminal.run_id).exists()


def test_network_findings_capture_only_first_party_failures_and_5xx():
    class FakePage:
        def __init__(self):
            self.handlers = {}

        def on(self, event, callback):
            self.handlers[event] = callback

    request_500 = type(
        "Request", (), {"url": "https://example.com/api/save?token=secret", "method": "POST"}
    )()
    response_500 = type("Response", (), {"status": 503, "request": request_500})()
    failed_request = type(
        "Request",
        (),
        {
            "url": "https://example.com/assets/app.js?signature=secret",
            "method": "GET",
            "failure": "net::ERR_CONNECTION_RESET",
        },
    )()
    third_party = type(
        "Request",
        (),
        {
            "url": "https://analytics.invalid/pixel",
            "method": "GET",
            "failure": "net::ERR_BLOCKED_BY_CLIENT",
        },
    )()
    page = FakePage()
    report = _report()

    _register_network_findings(page, report)
    page.handlers["response"](response_500)
    page.handlers["requestfailed"](failed_request)
    page.handlers["requestfailed"](third_party)

    assert [(finding.kind, finding.status_code) for finding in report.findings] == [
        ("network.http_5xx", 503),
        ("network.request_failed", None),
    ]
    assert report.findings[0].method == "POST"
    assert report.findings[0].url == "https://example.com/api/save"
    assert report.findings[1].url == "https://example.com/assets/app.js"
    assert "secret" not in report.model_dump_json()
