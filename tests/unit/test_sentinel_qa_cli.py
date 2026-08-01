import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from web_app.config import ConfigManager
from web_app.sentinel.qa_cli import cli
from web_app.sentinel.target_policy import TargetValidationError, ValidatedTarget


RUN_ID = "a" * 32


@pytest.fixture(autouse=True)
def _restore_runtime_config():
    config = ConfigManager()
    previous = (
        config.debug_mode,
        config.production_data_root,
        config.sentinel.allow_local_targets,
    )
    yield
    (
        config.debug_mode,
        config.production_data_root,
        config.sentinel.allow_local_targets,
    ) = previous


def _report(*, lifecycle="finished", verdict="pass", reason="No issues found"):
    return SimpleNamespace(
        run_id=RUN_ID,
        lifecycle=lifecycle,
        status=lifecycle,
        verdict=verdict,
        verdict_reason=reason,
        target_url="http://127.0.0.1:12345/feature",
        findings=[
            SimpleNamespace(severity="warning"),
            SimpleNamespace(severity="error"),
            SimpleNamespace(severity="info"),
        ],
        started_at=datetime(2026, 8, 1, 1, 0, tzinfo=timezone.utc).isoformat(),
        finished_at=datetime(2026, 8, 1, 1, 0, 2, tzinfo=timezone.utc).isoformat(),
    )


def _invoke_json(tmp_path: Path, report, extra_args=None):
    ConfigManager().production_data_root = tmp_path / "normal-data"
    args = [
        "run",
        "--target", "http://127.0.0.1:12345/feature",
        "--prompt", "Verify the feature saves a valid item.",
        "--title", "Feature smoke test",
        "--device", "desktop",
        "--demographic", "adult",
        "--limit", "1",
        "--report-url-base", "http://127.0.0.1:12346",
        "--json",
    ]
    args.extend(extra_args or [])
    validated = ValidatedTarget(
        url="http://127.0.0.1:12345/feature",
        hostname="127.0.0.1",
        addresses=("127.0.0.1",),
    )
    with (
        patch("web_app.sentinel.qa_cli.validate_public_web_url", return_value=validated),
        patch("web_app.sentinel.qa_cli.runner.create_run", return_value=report) as create,
        patch("web_app.sentinel.qa_cli.runner.execute_run", return_value=report) as execute,
        patch("web_app.sentinel.qa_cli._git_state", return_value=("abc123", True)),
        patch("web_app.sentinel.qa_cli.ensure_local_redis") as ensure_redis,
    ):
        result = CliRunner().invoke(cli, args)
    ensure_redis.assert_called_once_with()
    return result, create, execute


def test_json_run_prints_one_compact_payload_and_pass_exit_code(tmp_path):
    report = _report()

    result, create, execute = _invoke_json(tmp_path, report)

    assert result.exit_code == ConfigManager().sentinel.cli_exit_pass
    assert len(result.stdout.strip().splitlines()) == 1
    payload = json.loads(result.stdout)
    assert payload == {
        "schema_version": ConfigManager().sentinel.cli_schema_version,
        "run_id": RUN_ID,
        "lifecycle": "finished",
        "verdict": "pass",
        "reason": "No issues found",
        "target_url": "http://127.0.0.1:12345/feature",
        "report_path": str(tmp_path / "normal-data" / "sentinel" / "runs" / RUN_ID / "report.json"),
        "report_url": f"http://127.0.0.1:12346/sentinel/report/{RUN_ID}",
        "evidence_paths": [],
        "finding_counts": {"error": 1, "warning": 1, "info": 1},
        "duration_s": 2.0,
        "git_revision": "abc123",
        "working_tree_dirty": True,
    }
    create.assert_called_once()
    kwargs = create.call_args.kwargs
    assert kwargs["target"] == ValidatedTarget(
        url="http://127.0.0.1:12345/feature",
        hostname="127.0.0.1",
        addresses=("127.0.0.1",),
    )
    assert kwargs["prompt"] == "Verify the feature saves a valid item."
    assert kwargs["title"] == "Feature smoke test"
    assert kwargs["device"] == "desktop"
    assert kwargs["demographic"] == "adult"
    assert kwargs["limit_s"] == 60
    execute.assert_called_once_with(report)
    config = ConfigManager()
    assert config.debug_mode is False
    assert config.sentinel.allow_local_targets is True


def test_json_run_maps_fail_and_inconclusive_to_stable_exit_codes(tmp_path):
    failed, _, _ = _invoke_json(tmp_path, _report(verdict="fail", reason="Save failed"))
    inconclusive, _, _ = _invoke_json(
        tmp_path, _report(lifecycle="execution_error", verdict="inconclusive", reason="Browser failed")
    )

    assert failed.exit_code == ConfigManager().sentinel.cli_exit_fail
    assert inconclusive.exit_code == ConfigManager().sentinel.cli_exit_inconclusive


def test_non_finished_lifecycle_never_passes_or_product_fails(tmp_path):
    invalid_pass, _, _ = _invoke_json(
        tmp_path, _report(lifecycle="execution_error", verdict="pass")
    )
    timed_out_fail, _, _ = _invoke_json(
        tmp_path, _report(lifecycle="timed_out", verdict="fail")
    )

    assert invalid_pass.exit_code == ConfigManager().sentinel.cli_exit_inconclusive
    assert timed_out_fail.exit_code == ConfigManager().sentinel.cli_exit_inconclusive


def test_json_run_maps_cancelled_to_interrupted_exit_code(tmp_path):
    result, _, _ = _invoke_json(
        tmp_path,
        _report(lifecycle="cancelled", verdict="inconclusive", reason="Run interrupted"),
    )

    assert result.exit_code == ConfigManager().sentinel.cli_exit_interrupted


def test_run_forwards_optional_account_and_external_navigation_flags(tmp_path):
    result, create, _execute = _invoke_json(
        tmp_path,
        _report(),
        ["--allow-accounts", "--allow-external"],
    )

    assert result.exit_code == 0
    assert create.call_args.kwargs["allow_accounts"] is True
    assert create.call_args.kwargs["allow_external"] is True


def test_json_validation_failure_is_machine_readable_and_does_not_start_run(tmp_path):
    args = [
        "run", "--target", "ftp://invalid", "--prompt", "Check it",
        "--json",
    ]
    with (
        patch(
            "web_app.sentinel.qa_cli.validate_public_web_url",
            side_effect=TargetValidationError("URL must start with http:// or https://"),
        ),
        patch("web_app.sentinel.qa_cli.runner.create_run") as create,
        patch("web_app.sentinel.qa_cli.ensure_local_redis"),
    ):
        result = CliRunner().invoke(cli, args)

    assert result.exit_code == ConfigManager().sentinel.cli_exit_inconclusive
    assert json.loads(result.stdout) == {
        "schema_version": ConfigManager().sentinel.cli_schema_version,
        "run_id": None,
        "lifecycle": "execution_error",
        "verdict": "inconclusive",
        "reason": "URL must start with http:// or https://",
        "target_url": None,
        "report_path": None,
        "report_url": None,
        "evidence_paths": [],
        "finding_counts": {"error": 0, "warning": 0, "info": 0},
        "duration_s": None,
        "git_revision": None,
        "working_tree_dirty": None,
    }
    create.assert_not_called()


def test_run_help_does_not_offer_a_report_data_root_override():
    result = CliRunner().invoke(cli, ["run", "--help"])

    assert result.exit_code == 0
    assert "--qa-data-root" not in result.output


def test_compact_payload_strips_target_credentials_query_and_fragment(tmp_path):
    report = _report()
    report.target_url = "http://alice:secret@127.0.0.1:12345/feature?token=secret#part"

    result, _, _ = _invoke_json(tmp_path, report)

    assert result.exit_code == 0
    assert json.loads(result.stdout)["target_url"] == "http://127.0.0.1:12345/feature"
    assert "secret" not in result.stdout
