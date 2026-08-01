from __future__ import annotations

import json
import logging
import signal
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urlsplit, urlunsplit

import click
from git import Repo

from web_app.config import ConfigManager
from web_app.logging_utils import log_event
from web_app.redis_client import ensure_local_redis
from web_app.sentinel import runner
from web_app.sentinel.data_interface import DataInterface
from web_app.sentinel.target_policy import TargetValidationError, validate_public_web_url


def _value(value: Any) -> str:
    raw = getattr(value, "value", value)
    return str(raw or "")


def _git_state() -> tuple[str | None, bool | None]:
    try:
        repo = Repo(ConfigManager().project_dir, search_parent_directories=True)
        return repo.head.commit.hexsha, repo.is_dirty(untracked_files=True)
    except Exception:
        return None, None


def _duration_s(report: Any) -> float | None:
    if getattr(report, "duration_s", None) is not None:
        return round(float(report.duration_s), 3)
    started_at = getattr(report, "started_at", None)
    finished_at = getattr(report, "finished_at", None)
    if not started_at or not finished_at:
        return None
    try:
        started = datetime.fromisoformat(str(started_at))
        finished = datetime.fromisoformat(str(finished_at))
    except ValueError:
        return None
    return round(max(0.0, (finished - started).total_seconds()), 3)


def _finding_counts(report: Any) -> dict[str, int]:
    counts = {"error": 0, "warning": 0, "info": 0}
    for finding in getattr(report, "findings", ()):
        severity = _value(getattr(finding, "severity", "")).lower()
        if severity in counts:
            counts[severity] += 1
    return counts


def _safe_target_url(value: str) -> str:
    """Return a useful target reference without credentials or query secrets."""
    parsed = urlsplit(value)
    hostname = parsed.hostname or ""
    host = f"[{hostname}]" if ":" in hostname else hostname
    if parsed.port is not None:
        host = f"{host}:{parsed.port}"
    return urlunsplit((parsed.scheme, host, parsed.path or "/", "", ""))


def _evidence_paths(report: Any) -> list[str]:
    """Resolve only screenshot references contained by this run's directory."""
    data = DataInterface()
    run_dir = data.run_dir(str(report.run_id)).resolve()
    screenshots_dir = data.screenshots_dir(str(report.run_id)).resolve()
    evidence = []
    for raw_path in (
        list(getattr(report, "screenshots", ()) or ())
        + list(getattr(report, "annotated_screenshots", ()) or ())
    ):
        path = Path(str(raw_path))
        candidate = path.resolve() if path.is_absolute() else (run_dir / path).resolve()
        if not candidate.is_relative_to(screenshots_dir):
            continue
        value = str(candidate)
        if value not in evidence:
            evidence.append(value)
    return evidence


def _result_payload(
    report: Any,
    *,
    git_revision: str | None,
    working_tree_dirty: bool | None,
    report_url_base: str,
) -> dict[str, Any]:
    lifecycle = _value(
        getattr(report, "lifecycle", None) or getattr(report, "status", None)
    )
    verdict = _value(getattr(report, "verdict", None)) or "inconclusive"
    reason = str(getattr(report, "verdict_reason", None) or "")
    if not reason:
        reason = {
            "pass": "QA passed",
            "fail": "QA found a product failure",
        }.get(verdict, "Sentinel could not reach a conclusive verdict")
    run_id = str(report.run_id)
    report_url = None
    if report_url_base:
        report_url = f"{report_url_base.rstrip('/')}/sentinel/report/{run_id}"
    return {
        "schema_version": ConfigManager().sentinel.cli_schema_version,
        "run_id": run_id,
        "lifecycle": lifecycle,
        "verdict": verdict,
        "reason": reason,
        "target_url": _safe_target_url(str(getattr(report, "target_url", ""))),
        "report_path": str(DataInterface().report_path(run_id)),
        "report_url": report_url,
        "evidence_paths": _evidence_paths(report),
        "finding_counts": _finding_counts(report),
        "duration_s": _duration_s(report),
        "git_revision": git_revision,
        "working_tree_dirty": working_tree_dirty,
    }


def _error_payload(reason: str, run_id: str | None = None) -> dict[str, Any]:
    return {
        "schema_version": ConfigManager().sentinel.cli_schema_version,
        "run_id": run_id,
        "lifecycle": "execution_error",
        "verdict": "inconclusive",
        "reason": reason,
        "target_url": None,
        "report_path": None,
        "report_url": None,
        "evidence_paths": [],
        "finding_counts": {"error": 0, "warning": 0, "info": 0},
        "duration_s": None,
        "git_revision": None,
        "working_tree_dirty": None,
    }


def _exit_code(payload: dict[str, Any]) -> int:
    cfg = ConfigManager().sentinel
    lifecycle = payload.get("lifecycle")
    if lifecycle in {"cancelled", "interrupted"}:
        return cfg.cli_exit_interrupted
    if lifecycle == "finished" and payload.get("verdict") == "pass":
        return cfg.cli_exit_pass
    if lifecycle == "finished" and payload.get("verdict") == "fail":
        return cfg.cli_exit_fail
    return cfg.cli_exit_inconclusive


def _emit(payload: dict[str, Any], *, json_output: bool) -> None:
    if json_output:
        click.echo(json.dumps(payload, separators=(",", ":"), sort_keys=True))
        return
    click.echo(
        f"Sentinel {payload.get('verdict', 'inconclusive')}: "
        f"{payload.get('reason', '')}"
    )
    if payload.get("run_id"):
        click.echo(f"Run: {payload['run_id']}")
    if payload.get("report_path"):
        click.echo(f"Report: {payload['report_path']}")


@contextmanager
def _interrupt_signals(run_ref: dict[str, Any]) -> Iterator[None]:
    previous: dict[int, Any] = {}

    def handle_interrupt(_signum, _frame) -> None:
        # Keep signal-handler work minimal. execute_run owns durable terminal
        # state and cleanup when this KeyboardInterrupt reaches it.
        raise KeyboardInterrupt

    try:
        for signum in (signal.SIGINT, signal.SIGTERM):
            previous[signum] = signal.getsignal(signum)
            signal.signal(signum, handle_interrupt)
        yield
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


@click.group()
def cli() -> None:
    """Run Sentinel QA from a local coding agent."""


@cli.command("run")
@click.option("--target", required=True, help="Local app URL to test.")
@click.option("--prompt", required=True, help="Focused QA scenario and acceptance criteria.")
@click.option("--title", default="", help="Short report title.")
@click.option(
    "--device",
    type=click.Choice(tuple(ConfigManager().sentinel.device_profiles)),
    default=ConfigManager().sentinel.default_device,
    show_default=True,
)
@click.option(
    "--demographic",
    type=click.Choice(tuple(ConfigManager().sentinel.demographic_personas)),
    default=ConfigManager().sentinel.default_demographic,
    show_default=True,
)
@click.option(
    "--limit",
    "limit_mins",
    type=click.IntRange(
        ConfigManager().sentinel.min_limit_mins,
        ConfigManager().sentinel.max_limit_mins,
    ),
    default=ConfigManager().sentinel.default_limit_mins,
    show_default=True,
    help="Maximum run duration in minutes.",
)
@click.option("--allow-accounts", is_flag=True, help="Permit account-related flows.")
@click.option("--allow-external", is_flag=True, help="Permit off-target navigation.")
@click.option(
    "--report-url-base",
    default=None,
    help="Optional base URL of the local Sentinel report UI.",
)
@click.option("--json", "json_output", is_flag=True, help="Print one JSON result to stdout.")
def run_command(
    target: str,
    prompt: str,
    title: str,
    device: str,
    demographic: str,
    limit_mins: int,
    allow_accounts: bool,
    allow_external: bool,
    report_url_base: str | None,
    json_output: bool,
) -> None:
    """Execute one Sentinel run synchronously and wait for its verdict."""
    cfg = ConfigManager()
    # Agent-started reports intentionally share the normal Sentinel store so
    # the ordinary admin UI can observe them. This process never starts the web
    # app or mutates other subapp data directly.
    cfg.debug_mode = False
    cfg.sentinel.allow_local_targets = True
    report_url_base = report_url_base if report_url_base is not None else cfg.sentinel.report_url_base
    git_revision, working_tree_dirty = _git_state()
    run_ref: dict[str, Any] = {}

    try:
        with _interrupt_signals(run_ref):
            ensure_local_redis()
            validated_target = validate_public_web_url(target)
            report = runner.create_run(
                target=validated_target,
                prompt=prompt,
                limit_s=limit_mins * 60,
                title=title,
                allow_accounts=allow_accounts,
                allow_external=allow_external,
                device=device,
                demographic=demographic,
            )
            run_ref["report"] = report
            report = runner.execute_run(report)
        payload = _result_payload(
            report,
            git_revision=git_revision,
            working_tree_dirty=working_tree_dirty,
            report_url_base=report_url_base,
        )
    except KeyboardInterrupt:
        report = run_ref.get("report")
        if report is not None:
            payload = _result_payload(
                report,
                git_revision=git_revision,
                working_tree_dirty=working_tree_dirty,
                report_url_base=report_url_base,
            )
            payload["lifecycle"] = "interrupted"
            payload["verdict"] = "inconclusive"
            payload["reason"] = str(
                getattr(report, "verdict_reason", None) or "Run interrupted"
            )
        else:
            payload = _error_payload("Run interrupted")
            payload["lifecycle"] = "interrupted"
    except TargetValidationError as error:
        payload = _error_payload(str(error))
    except Exception as error:
        log_event(
            "sentinel",
            "sentinel.cli_run_failed",
            level=logging.ERROR,
            run_id=str(getattr(run_ref.get("report"), "run_id", "")) or None,
            error_type=type(error).__name__,
            exc_info=error,
        )
        payload = _error_payload(
            "Sentinel execution failed",
            str(getattr(run_ref.get("report"), "run_id", "")) or None,
        )

    _emit(payload, json_output=json_output)
    raise click.exceptions.Exit(_exit_code(payload))
