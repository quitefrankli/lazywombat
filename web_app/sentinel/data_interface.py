from __future__ import annotations

import re
import shutil
import logging
from datetime import datetime, timezone
from pathlib import Path

from web_app.config import ConfigManager
from web_app.data_interface import DataInterface as BaseDataInterface
from web_app.logging_utils import log_event
from web_app.redis_client import rmw_lock
from web_app.sentinel.models import ExecutionStatus, Report, RunStatus, RunVerdict


_RUN_ID_RE = re.compile(r"^[a-f0-9]{32}$")
_ACTIVE_LIFECYCLES = {
    ExecutionStatus.QUEUED,
    ExecutionStatus.RUNNING,
    ExecutionStatus.SUMMARIZING,
}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class DataInterface(BaseDataInterface):
    def __init__(self) -> None:
        super().__init__()
        self.sentinel_dir = ConfigManager().save_data_path / "sentinel"
        self.runs_dir = self.sentinel_dir / "runs"

    def _safe_run_id(self, run_id: str) -> str:
        if not _RUN_ID_RE.match(run_id):
            raise ValueError("Invalid run id")
        return run_id

    def run_dir(self, run_id: str) -> Path:
        return self.runs_dir / self._safe_run_id(run_id)

    def report_path(self, run_id: str) -> Path:
        return self.run_dir(run_id) / "report.json"

    def screenshots_dir(self, run_id: str) -> Path:
        return self.run_dir(run_id) / "screenshots"

    def screenshot_path(self, run_id: str, index: int) -> Path:
        return self.screenshots_dir(run_id) / f"step-{index:02d}.png"

    def annotated_screenshot_path(self, run_id: str, index: int) -> Path:
        return self.screenshots_dir(run_id) / f"step-{index:02d}-annot.png"

    def screenshot_thumbnail_path(self, run_id: str, filename: str) -> Path:
        return self.screenshots_dir(run_id) / "thumbs" / filename

    def _save_report(
        self, report: Report, *, touch_updated_at: bool = True
    ) -> Report:
        """Persist a runner snapshot under the report's path-keyed lock.

        A stale in-memory active snapshot must not resurrect a report another
        process already made terminal (for example abandoned-run recovery).
        """
        run_id = self._safe_run_id(report.run_id)
        path = self.report_path(run_id)
        with rmw_lock(self._model_lock_name(path)):
            persisted = self.load_model(path, Report, sync=False)
            if (
                persisted is not None
                and persisted.lifecycle not in _ACTIVE_LIFECYCLES
                and report.lifecycle in _ACTIVE_LIFECYCLES
            ):
                return persisted
            if touch_updated_at:
                report.updated_at = utc_now_iso()
            # Credentials/card are written to disk only when the user opted in
            # via the matching remember_* flag. Otherwise they remain in the
            # runner's memory for this execution only.
            exclude = set()
            if not report.remember_account:
                exclude.add("account_credentials")
            if not report.remember_card:
                exclude.add("card_details")
            self.atomic_write(
                path,
                data=report.model_dump_json(indent=4, exclude=exclude or None),
                mode="w",
                encoding="utf-8",
            )
        return report

    def edit_report(self, run_id: str):
        """Transactionally edit one persisted report without slow work."""
        return self.edit_model(self.report_path(run_id), Report)

    def _recover_if_abandoned(self, report: Report) -> Report:
        if report.lifecycle not in _ACTIVE_LIFECYCLES:
            return report
        try:
            updated_at = datetime.fromisoformat(report.updated_at)
            if updated_at.tzinfo is None:
                updated_at = updated_at.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            return report
        stale_seconds = (datetime.now(timezone.utc) - updated_at).total_seconds()
        if stale_seconds <= ConfigManager().sentinel.abandoned_run_timeout_s:
            return report

        now = utc_now_iso()
        with self.edit_report(report.run_id) as persisted:
            # Re-check inside the distributed lock in case the executor wrote a
            # heartbeat after our initial read.
            if persisted.lifecycle not in _ACTIVE_LIFECYCLES:
                return persisted
            try:
                persisted_updated_at = datetime.fromisoformat(persisted.updated_at)
                if persisted_updated_at.tzinfo is None:
                    persisted_updated_at = persisted_updated_at.replace(tzinfo=timezone.utc)
            except (TypeError, ValueError):
                return persisted
            if (
                datetime.now(timezone.utc) - persisted_updated_at
            ).total_seconds() <= ConfigManager().sentinel.abandoned_run_timeout_s:
                return persisted
            persisted.lifecycle = ExecutionStatus.ABANDONED
            persisted.verdict = RunVerdict.INCONCLUSIVE
            persisted.verdict_reason_code = "abandoned"
            persisted.verdict_reason = (
                "The run was abandoned after its executor stopped updating progress."
            )
            persisted.status = RunStatus.FAILED
            persisted.run_outcome = RunStatus.FAILED
            persisted.finished_at = now
            persisted.updated_at = now
        log_event(
            "sentinel",
            "sentinel.run_abandoned",
            level=logging.WARNING,
            user=report.owner or None,
            run_id=report.run_id,
            batch_id=report.batch_id or None,
            reason="stale_active_run",
        )
        return self.load_model(self.report_path(report.run_id), Report, sync=False) or report

    def load_report(self, run_id: str) -> Report | None:
        report = self.load_model(self.report_path(run_id), Report, sync=False)
        return self._recover_if_abandoned(report) if report is not None else None

    def list_reports(self) -> list[Report]:
        if not self.runs_dir.exists():
            return []
        reports = []
        for path in self.runs_dir.glob("*/report.json"):
            try:
                report = Report.model_validate_json(path.read_text(encoding="utf-8"))
                reports.append(self._recover_if_abandoned(report))
            except (OSError, ValueError):
                continue
        reports.sort(key=lambda item: item.created_at, reverse=True)
        return reports

    def prune_reports(self) -> None:
        max_runs = ConfigManager().sentinel.max_retained_runs
        if not self.runs_dir.exists():
            return
        terminal_dirs = []
        for run_dir in self.runs_dir.iterdir():
            if not run_dir.is_dir():
                continue
            try:
                report = self.load_model(run_dir / "report.json", Report, sync=False)
            except (OSError, ValueError):
                continue
            if report is not None and report.lifecycle not in _ACTIVE_LIFECYCLES:
                terminal_dirs.append(run_dir)
        terminal_dirs.sort(key=lambda path: path.stat().st_mtime, reverse=True)
        for old_dir in terminal_dirs[max_runs:]:
            path = old_dir / "report.json"
            with rmw_lock(self._model_lock_name(path)):
                current = self.load_model(path, Report, sync=False)
                if current is None or current.lifecycle in _ACTIVE_LIFECYCLES:
                    continue
                shutil.rmtree(old_dir)

    def delete_run(self, run_id: str) -> bool:
        run_dir = self.run_dir(run_id)
        if not run_dir.exists():
            return False
        path = self.report_path(run_id)
        with rmw_lock(self._model_lock_name(path)):
            report = self.load_model(path, Report, sync=False)
            if report is None or report.lifecycle in _ACTIVE_LIFECYCLES:
                return False
            shutil.rmtree(run_dir)
        return True

    def delete_user_data(self, user) -> None:
        return None

    def backup_data(self, backup_dir: Path) -> None:
        self._backup_subtree(self.sentinel_dir, backup_dir, "sentinel")
