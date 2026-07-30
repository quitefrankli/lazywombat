from __future__ import annotations

import json
import logging
import re
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import urljoin, urlparse

from web_app.config import ConfigManager
from web_app.redis_client import get_redis
from web_app.sentinel.actions import ActionValidationError, AgentAction, parse_agent_action
from web_app.sentinel.data_interface import DataInterface, utc_now_iso
from web_app.sentinel.models import (
    AccountCredentials,
    ActionResult,
    CardDetails,
    Finding,
    Report,
    RunStatus,
    Step,
)
from web_app.sentinel.providers import _get_provider
from web_app.sentinel.target_policy import ValidatedTarget, validate_public_web_url
from web_app.logging_utils import log_event


_active_runs: dict[str, Report] = {}
_active_lock = threading.RLock()

_CANCEL_PREFIX = "nabicat:sentinel:cancel:"


def render_report_pdf(html: str, stylesheet_paths: tuple[Path, ...] = ()) -> bytes:
    """Render an HTML string to PDF bytes using headless Chromium (Playwright)."""
    from playwright.sync_api import sync_playwright

    cfg = ConfigManager().sentinel
    # Chromium's header/footer templates do NOT inherit the page <style>, so
    # the footer must carry fully inline styling. A near-empty header_template
    # suppresses Chromium's default date/title header.
    footer_template = (
        '<div style="font-family: \'Nunito\', sans-serif; font-size:7.5pt; color:#8A9A8A; '
        'width:100%; padding:0 14mm; display:flex; justify-content:space-between;">'
        f'<span>{cfg.pdf_footer_label} &middot; <span class="date"></span></span>'
        '<span>Page <span class="pageNumber"></span> of <span class="totalPages"></span></span>'
        "</div>"
    )
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            context = browser.new_context()
            page = context.new_page()
            page.set_content(html, wait_until="load")
            for stylesheet_path in stylesheet_paths:
                page.add_style_tag(path=str(stylesheet_path))
            return page.pdf(
                format="A4",
                print_background=True,
                display_header_footer=True,
                header_template="<span></span>",
                footer_template=footer_template,
                margin={
                    "top": cfg.pdf_margin_top,
                    "bottom": cfg.pdf_margin_bottom,
                    "left": cfg.pdf_margin_left,
                    "right": cfg.pdf_margin_right,
                },
            )
        finally:
            browser.close()


def request_cancel(run_id: str) -> bool:
    """Signal a run to cancel.

    Redis-backed so the cancel request can be served by a different gunicorn
    worker than the one running the browser thread. Only flags runs that are
    still active (their cancel key was armed by start_run); returns False for
    unknown/finished runs. Fails safe (returns False) if Redis is unreachable.
    """
    try:
        # Only set if the run is still active — start_run arms the key with a
        # sentinel value; NX-less set would resurrect a cleared/expired run.
        updated = get_redis().set(_CANCEL_PREFIX + run_id, b"1", xx=True, keepttl=True)
    except Exception as error:
        log_event(
            "sentinel", "sentinel.cancel_signal_failed",
            level=logging.ERROR, run_id=run_id, exc_info=error,
            error_type=type(error).__name__,
        )
        return False
    return bool(updated)


def _is_cancelled(run_id: str) -> bool:
    try:
        return get_redis().get(_CANCEL_PREFIX + run_id) == b"1"
    except Exception as error:
        log_event(
            "sentinel", "sentinel.cancel_check_failed",
            level=logging.ERROR, run_id=run_id, exc_info=error,
            error_type=type(error).__name__,
        )
        return False


_ACTIVE_STATUSES = {RunStatus.QUEUED, RunStatus.RUNNING, RunStatus.SUMMARIZING}


def _save(report: Report) -> None:
    # The cache snapshot is a deep copy: the run loop keeps mutating `report`
    # (appending steps/findings) after this returns, and a shallow copy would
    # alias those lists into the cached entry, so a concurrent reader via
    # get_run() could observe half-written state.
    DataInterface()._save_report(report)
    with _active_lock:
        _active_runs[report.run_id] = report.model_copy(deep=True)


def get_run(run_id: str) -> Report | None:
    with _active_lock:
        if run_id in _active_runs:
            # Hand callers their own deep copy so they can't mutate the cache.
            return _active_runs[run_id].model_copy(deep=True)
    try:
        return DataInterface().load_report(run_id)
    except ValueError:
        return None


def delete_run(run_id: str) -> bool:
    report = get_run(run_id)
    if report is None or report.status in _ACTIVE_STATUSES:
        return False
    try:
        deleted = DataInterface().delete_run(run_id)
    except ValueError:
        return False
    if deleted:
        with _active_lock:
            _active_runs.pop(run_id, None)
        get_redis().delete(_CANCEL_PREFIX + run_id)
    return deleted


def start_run(
    target: ValidatedTarget,
    prompt: str,
    limit_s: int,
    title: str = "",
    allow_accounts: bool = False,
    allow_external: bool = False,
    additional_domains: list[str] | None = None,
    allow_financial: bool = False,
    card_details: dict | None = None,
    account_credentials: dict | None = None,
    remember_account: bool = False,
    remember_card: bool = False,
    device: str = "",
    demographic: str = "",
    owner: str = "",
    batch_id: str = "",
    batch_label: str = "",
) -> dict:
    run_id = uuid.uuid4().hex
    now = utc_now_iso()
    title = _clean_title(title)
    cfg = ConfigManager()
    if device not in cfg.sentinel.device_profiles:
        device = cfg.sentinel.default_device
    if demographic not in cfg.sentinel.demographic_personas:
        demographic = cfg.sentinel.default_demographic
    report = Report(
        run_id=run_id,
        status=RunStatus.QUEUED,
        owner=str(owner or ""),
        batch_id=str(batch_id or ""),
        batch_label=str(batch_label or ""),
        target_url=target.url,
        target_hostname=target.hostname,
        prompt=prompt,
        title=title,
        allow_accounts=bool(allow_accounts),
        allow_external=bool(allow_external),
        additional_domains=list(additional_domains or []),
        allow_financial=bool(allow_financial and card_details),
        device=device,
        demographic=demographic,
        limit_s=limit_s,
        created_at=now,
        updated_at=now,
        remember_account=bool(remember_account),
        remember_card=bool(remember_card),
    )
    if allow_financial and card_details:
        report.card_details = CardDetails(
            card_number=card_details.get("card_number", ""),
            expiry=card_details.get("expiry", ""),
            cvv=card_details.get("cvv", ""),
        )
    if allow_accounts and account_credentials:
        report.account_credentials = AccountCredentials(
            username=account_credentials.get("username", ""),
            password=account_credentials.get("password", ""),
            extras=dict(account_credentials.get("extras") or {}),
        )
    _save(report)
    log_event(
        "sentinel", "sentinel.run_queued",
        user=report.owner or None, run_id=run_id, batch_id=batch_id or None,
    )
    # Arm the cancel flag as "not cancelled" so request_cancel (SET XX) can flip
    # it. TTL outlives the longest run and self-cleans if the process dies.
    get_redis().set(
        _CANCEL_PREFIX + run_id, b"0", ex=ConfigManager().sentinel.cancel_flag_ttl_s
    )
    thread = threading.Thread(target=_run_background, args=(report,), daemon=True)
    thread.start()
    return {"run_id": run_id, "status": "queued"}


def _run_background(report: Report) -> None:
    started_ns = time.monotonic_ns()
    report.status = RunStatus.RUNNING
    report.started_at = utc_now_iso()
    if not report.title:
        report.title = _generate_title(report.target_url, report.prompt, report.target_hostname)
    _save(report)
    log_event(
        "sentinel", "sentinel.run_started",
        user=report.owner or None, run_id=report.run_id,
        batch_id=report.batch_id or None,
    )
    try:
        _execute_browser_run(report)
        if _is_cancelled(report.run_id):
            report.status = RunStatus.CANCELLED
        outcome_status = RunStatus.COMPLETED if report.status == RunStatus.RUNNING else report.status
        report.run_outcome = outcome_status
        if outcome_status == RunStatus.CANCELLED:
            report.final_report = "## Summary\n\nThis run was cancelled before it finished."
            report.status = outcome_status
        else:
            report.status = RunStatus.SUMMARIZING
            _save(report)
            _add_final_report(report)
            if outcome_status == RunStatus.COMPLETED:
                login_fail_reason = _detect_login_failure(report)
                if login_fail_reason:
                    outcome_status = RunStatus.FAILED
                    report.verdict_reason = login_fail_reason
                    _add_finding(report, "error", "Login failed", login_fail_reason)
                else:
                    outcome_status = _classify_run_verdict(report)
                report.run_outcome = outcome_status
            report.status = outcome_status
    except Exception as e:
        log_event(
            "sentinel", "sentinel.run_failed",
            level=logging.ERROR, user=report.owner or None,
            run_id=report.run_id, batch_id=report.batch_id or None,
            exc_info=e, error_type=type(e).__name__,
        )
        report.status = RunStatus.FAILED
        report.error = str(e)
    finally:
        report.finished_at = utc_now_iso()
        _save(report)
        get_redis().delete(_CANCEL_PREFIX + report.run_id)
        DataInterface().prune_reports()
        log_event(
            "sentinel", "sentinel.run_finished",
            level=logging.ERROR if report.status == RunStatus.FAILED else logging.INFO,
            user=report.owner or None,
            run_id=report.run_id,
            batch_id=report.batch_id or None,
            status=report.status.value,
            steps=len(report.steps),
            findings=len(report.findings),
            duration_ms=round((time.monotonic_ns() - started_ns) / 1_000_000, 3),
        )


def _host_allowed(hostname: str, target_hostname: str) -> bool:
    hostname = hostname.lower().rstrip(".")
    target_hostname = target_hostname.lower().rstrip(".")
    return (
        hostname == target_hostname
        or hostname == f"www.{target_hostname}"
        or f"www.{hostname}" == target_hostname
    )


def _navigation_host_allowed(hostname: str, target_hostname: str, additional_domains: list[str] | None = None) -> bool:
    return _host_allowed(hostname, target_hostname) or any(
        _host_allowed(hostname, domain) for domain in (additional_domains or [])
    )


def _execute_browser_run(report: Report) -> None:
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
    from playwright.sync_api import sync_playwright

    target = ValidatedTarget(url=report.target_url, hostname=report.target_hostname)
    deadline = time.monotonic() + int(report.limit_s)
    cfg = ConfigManager()

    from playwright_stealth import Stealth

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=True,
            args=list(cfg.sentinel.browser_launch_args),
        )
        context = None
        try:
            device_key = str(report.device or cfg.sentinel.default_device)
            profile_name = cfg.sentinel.device_profiles.get(device_key, "")
            context_kwargs: dict = {"ignore_https_errors": cfg.debug_mode}
            if profile_name:
                context_kwargs.update(playwright.devices[profile_name])
            else:
                context_kwargs["viewport"] = {
                    "width": cfg.sentinel.browser_width_px,
                    "height": cfg.sentinel.browser_height_px,
                }
                context_kwargs["user_agent"] = cfg.sentinel.browser_desktop_user_agent
            # Playwright's Chromium uses NSS / system trust stores, not
            # $SSL_CERT_FILE, so dev environments without a populated NSS
            # DB hit ERR_CERT_AUTHORITY_INVALID on perfectly valid public
            # sites. Bypass cert validation in debug mode only.
            context = browser.new_context(**context_kwargs)
            # Patches navigator.webdriver, plugins, languages, WebGL vendor,
            # chrome.runtime, and a few other headless-Chromium tells. Cuts
            # through most basic Cloudflare/Akamai JS challenges. Doesn't
            # require a real GPU — the WebGL spoof only patches JS getters.
            Stealth().apply_stealth_sync(context)
            page = context.new_page()
            page.set_default_timeout(cfg.sentinel.browser_default_timeout_ms)
            page.on("console", lambda msg: _add_finding(report, "info", "Console", msg.text))
            page.on("pageerror", lambda err: _add_finding(report, "error", "Page error", str(err)))

            allow_external = bool(report.allow_external)
            additional_domains = list(report.additional_domains or [])

            def guard_route(route):
                req_url = route.request.url
                try:
                    checked = validate_public_web_url(req_url)
                    is_navigation = route.request.is_navigation_request()
                    if (
                        is_navigation
                        and not allow_external
                        and not _navigation_host_allowed(checked.hostname, target.hostname, additional_domains)
                    ):
                        route.abort()
                        return
                except Exception:
                    route.abort()
                    return
                route.continue_()

            initial_nav = _goto_page(page, target.url, target)
            if not initial_nav.get("ok"):
                raise RuntimeError(initial_nav.get("error", "Initial navigation failed"))
            if initial_nav.get("warning"):
                _add_finding(report, "warning", "Slow page load", initial_nav["warning"])
            final_initial = validate_public_web_url(page.url)
            if not _host_allowed(final_initial.hostname, target.hostname):
                raise RuntimeError("Initial navigation redirected outside target host")
            page.route("**/*", guard_route)

            # step-00.png: initial page state, before any agent action.
            _capture_screenshot(page, report, 0)

            while time.monotonic() < deadline and len(report.steps) < cfg.sentinel.max_steps:
                if _is_cancelled(report.run_id):
                    break
                next_step_index = len(report.steps) + 1
                observation = _observe_page(page)
                known_ids = {item["id"] for item in observation["elements"]}
                # The screenshot the agent reasons about is the *current* page
                # state — i.e. step-(N-1).png, the post-action result of the
                # prior step (or step-00 on the first iteration).
                current_screenshot = f"screenshots/step-{(next_step_index - 1):02d}.png"
                annotated = _capture_annotated_screenshot(report, current_screenshot, observation, next_step_index - 1)
                observation["screenshot"] = current_screenshot

                image_paths = _annotated_image_paths(report, annotated, current_screenshot)
                action = _request_agent_action(report, observation, image_paths, allow_external, known_ids)
                if action is None:
                    break
                if _finish_requires_more_scroll(report, action, observation):
                    action = AgentAction(
                        action="scroll",
                        value="down",
                        reason=(
                            "finish deferred: the prompt asks for full-page coverage and this page "
                            "still has unseen content below"
                        ),
                    )

                result = _apply_action(
                    page,
                    action,
                    target,
                    allow_external=allow_external,
                    additional_domains=additional_domains,
                )
                # Set/clear the peek-pending flag so _annotated_image_paths
                # knows whether to attach the raw screenshot next iteration.
                report._peek_pending = (action.action == "peek")
                _record_step(report, action.action, action.reason, result)
                # step-N.png: state AFTER step N's action — this is what gets
                # surfaced in the final report so step number matches outcome.
                _capture_screenshot(page, report, next_step_index)
                _save(report)
                stuck = _detect_click_loop(report)
                if action.action == "finish":
                    break
                if stuck:
                    _record_step(
                        report,
                        "finish",
                        "stuck: agent looped on broken/self-referential controls and was force-stopped",
                        {"ok": True, "url": result.get("url", "")},
                    )
                    _capture_screenshot(page, report, next_step_index + 1)
                    _save(report)
                    break

            if time.monotonic() >= deadline:
                report.status = RunStatus.TIMED_OUT
        except PlaywrightTimeoutError as e:
            _add_finding(report, "error", "Browser timeout", str(e)[:500])
            report.status = RunStatus.TIMED_OUT
        finally:
            if context is not None:
                context.close()
            browser.close()


def _goto_page(
    page,
    url: str,
    target: ValidatedTarget,
    allow_external: bool = False,
    additional_domains: list[str] | None = None,
) -> dict:
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

    cfg = ConfigManager()
    checked = validate_public_web_url(url)
    if not allow_external and not _navigation_host_allowed(checked.hostname, target.hostname, additional_domains):
        return {"ok": False, "error": "Navigation outside target host blocked", "url": checked.url}
    page.goto(checked.url, wait_until="commit", timeout=cfg.sentinel.navigation_timeout_ms)
    try:
        page.wait_for_load_state("domcontentloaded", timeout=cfg.sentinel.navigation_timeout_ms)
    except PlaywrightTimeoutError:
        return {"ok": True, "warning": "Timed out waiting for DOMContentLoaded", "url": page.url}
    return {"ok": True, "url": page.url}


def _clean_title(text: str) -> str:
    cfg = ConfigManager()
    text = " ".join(str(text or "").split()).strip().strip('"').strip("'")
    if len(text) > cfg.sentinel.title_max_chars:
        text = text[: cfg.sentinel.title_max_chars].rstrip()
    return text


def _generate_title(target_url: str, prompt: str, target_hostname: str = "") -> str:
    payload = json.dumps(
        {
            "target_url": target_url,
            "user_prompt": prompt or "Explore and test the site's main unauthenticated flows.",
        },
        indent=2,
    )
    try:
        return _clean_title(_get_provider().title_text(payload)) or _fallback_title(target_url, target_hostname)
    except Exception as e:
        log_event(
            "sentinel", "sentinel.title_generation_failed",
            level=logging.WARNING, exc_info=e, error_type=type(e).__name__,
        )
        return _fallback_title(target_url, target_hostname)


def _fallback_title(target_url: str, target_hostname: str = "") -> str:
    return _clean_title(target_hostname or target_url or "Sentinel run")


def _add_finding(report: Report, severity: str, title: str, detail: str) -> None:
    max_chars = ConfigManager().sentinel.finding_detail_max_chars
    detail = " ".join(str(detail).split())
    if len(detail) > max_chars:
        detail = f"{detail[:max_chars].rstrip()}..."
    report.findings.append(Finding(severity=severity, title=title, detail=detail))


_VERDICT_FAIL_REASON_MAX_CHARS = 300


def _classify_run_verdict(report: Report) -> RunStatus:
    """Ask the LLM whether the run actually fulfilled the user's prompt.

    Returns the new run status: COMPLETED on pass, FAILED on fail. On any
    error or unparseable response, falls back to COMPLETED (the existing
    behavior) so this never blocks a real success.
    """
    try:
        raw = _get_provider().verdict_text(_verdict_prompt(report))
        parsed = _parse_verdict_payload(raw)
    except Exception as e:
        log_event(
            "sentinel", "sentinel.verdict_classification_failed",
            level=logging.WARNING, user=report.owner or None,
            run_id=report.run_id, exc_info=e, error_type=type(e).__name__,
        )
        return RunStatus.COMPLETED
    if not parsed:
        return RunStatus.COMPLETED
    verdict, reason = parsed
    if verdict != "fail":
        return RunStatus.COMPLETED
    report.verdict_reason = reason[:_VERDICT_FAIL_REASON_MAX_CHARS]
    _add_finding(report, "warning", "Run did not fulfill prompt", reason)
    return RunStatus.FAILED


def _verdict_prompt(report: Report) -> str:
    payload = {
        "original_prompt": report.prompt or "",
        "target_url": report.target_url,
        "allow_accounts": bool(report.allow_accounts),
        "allow_external": bool(report.allow_external),
        "additional_domains": list(report.additional_domains or []),
        "steps": [
            {"action": step.action, "reason": step.reason, "result": step.result.model_dump(exclude_none=True)}
            for step in report.steps
        ],
        "findings": [f.model_dump() for f in report.findings],
        "final_report": report.final_report or "",
    }
    return json.dumps(payload, indent=2)


def _parse_verdict_payload(raw: str) -> tuple[str, str] | None:
    text = str(raw or "").strip()
    if not text:
        return None
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    verdict = str(data.get("verdict", "")).strip().lower()
    if verdict not in {"pass", "fail"}:
        return None
    reason = str(data.get("reason", "")).strip() or "No reason provided."
    return verdict, reason


def _add_final_report(report: Report) -> None:
    picked = _pick_final_report_screenshots(report)
    try:
        text = _get_provider().final_report_text(
            _final_report_prompt(report, picked),
            image_paths=_final_report_image_paths(report, picked),
        )
    except Exception as e:
        log_event(
            "sentinel", "sentinel.final_report_generation_failed",
            level=logging.WARNING, user=report.owner or None,
            run_id=report.run_id, exc_info=e, error_type=type(e).__name__,
        )
        text = _fallback_final_report(report)
    text = _ensure_summary_heading(text)
    report.final_report = _truncate_text(text, ConfigManager().sentinel.final_report_max_chars)
    _save(report)


_SUMMARY_HEADING_RE = re.compile(r"^\s*#{1,6}\s*summary\b", re.IGNORECASE)


def _ensure_summary_heading(text: str) -> str:
    body = str(text or "").lstrip()
    if not body:
        return "## Summary\n\nNo report content was generated."
    if _SUMMARY_HEADING_RE.match(body):
        return body
    return f"## Summary\n\n{body}"


def _screenshot_manifest(report: Report) -> list[dict]:
    """Build a [{filename, produced_by, url}, ...] manifest of all screenshots
    in the run, where produced_by names the action that produced that frame
    (or 'initial' for step-00.png).
    """
    steps_by_index = {int(s.index): s for s in report.steps}
    entries = []
    for shot in report.screenshots or []:
        filename = Path(str(shot)).name
        m = re.match(r"^step-(\d{2})\.png$", filename)
        if not m:
            continue
        idx = int(m.group(1))
        if idx == 0:
            entries.append({"filename": filename, "produced_by": "initial", "url": report.target_url})
            continue
        step = steps_by_index.get(idx)
        if step is None:
            entries.append({"filename": filename, "produced_by": "", "url": ""})
            continue
        entries.append({
            "filename": filename,
            "produced_by": f"{step.action}: {step.reason}".strip(": "),
            "url": step.result.url,
        })
    return entries


def _pick_final_report_screenshots(report: Report) -> list[str]:
    """Ask a cheap LLM call which screenshots to attach to the final-report
    call. Returns a list of filenames (e.g. ['step-04.png', 'step-17.png']).
    Falls back to the last N screenshots on any error.
    """
    cfg = ConfigManager().sentinel
    manifest = _screenshot_manifest(report)
    if not manifest:
        return []
    budget = max(1, cfg.final_report_picker_budget)
    available = [e["filename"] for e in manifest]
    fallback = available[-min(budget, len(available)):]
    payload = json.dumps({
        "original_prompt": report.prompt or "",
        "target_url": report.target_url,
        "additional_domains": list(report.additional_domains or []),
        "status": report.run_outcome or report.status,
        "budget": budget,
        "available_screenshots": manifest,
        "findings": [
            {"severity": f.severity, "title": f.title, "detail": f.detail}
            for f in report.findings if f.severity != "info"
        ],
    }, indent=2)
    try:
        raw = _get_provider().screenshot_picker_text(payload)
    except Exception as e:
        log_event(
            "sentinel", "sentinel.screenshot_picker_failed",
            level=logging.WARNING, user=report.owner or None,
            run_id=report.run_id, exc_info=e, error_type=type(e).__name__,
        )
        return fallback
    chosen = _parse_picker_payload(raw, set(available), budget)
    return chosen or fallback


def _parse_picker_payload(raw: str, allowed: set[str], budget: int) -> list[str]:
    text = str(raw or "").strip()
    if not text:
        return []
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return []
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return []
    raw_list = data.get("screenshots") if isinstance(data, dict) else None
    if not isinstance(raw_list, list):
        return []
    seen: list[str] = []
    for item in raw_list:
        name = str(item).strip()
        if name in allowed and name not in seen:
            seen.append(name)
        if len(seen) >= budget:
            break
    return seen


def _final_report_prompt(report: Report, picked: list[str] | None = None) -> str:
    manifest = _screenshot_manifest(report)
    payload = {
        "original_prompt": report.prompt or "Explore and test the site's main unauthenticated flows.",
        "target_url": report.target_url,
        "additional_domains": list(report.additional_domains or []),
        "status": report.run_outcome or report.status,
        "steps": [
            {"action": step.action, "reason": step.reason, "result": step.result.model_dump(exclude_none=True)}
            for step in report.steps
        ],
        "findings": [f.model_dump() for f in report.findings],
        "screenshots": [e["filename"] for e in manifest],
        "screenshot_manifest": manifest,
        "attached_screenshots": picked or [],
    }
    return json.dumps(payload, indent=2)


def _final_report_image_paths(report: Report, picked: list[str] | None = None) -> list[Path]:
    cfg = ConfigManager().sentinel
    if picked:
        names = list(picked)
    else:
        # Fallback: last N raw screenshots (preserves prior behavior if picker
        # is disabled or returns nothing).
        max_images = cfg.final_report_max_images
        names = [Path(str(s)).name for s in (report.screenshots or [])[-max_images:]]
    paths = []
    seen: set[str] = set()
    for name in names:
        if name in seen:
            continue
        seen.add(name)
        path = DataInterface().screenshots_dir(report.run_id) / name
        if path.exists():
            paths.append(path)
    return paths


def _fallback_final_report(report: Report) -> str:
    prompt = report.prompt or "the requested public-site QA pass"
    findings = report.findings
    if findings:
        finding_text = "; ".join(f"{item.title or 'Finding'}: {item.detail}" for item in findings[:5])
        return f"Sentinel tested {report.target_url} for {prompt}. Key findings: {finding_text}"
    return f"Sentinel tested {report.target_url} for {prompt}. No findings were recorded during the run."


def _truncate_text(text: str, max_chars: int) -> str:
    text = str(text).strip()
    if len(text) <= max_chars:
        return text
    return f"{text[:max_chars].rstrip()}..."


def _capture_screenshot(page, report: Report, index: int) -> str | None:
    """Capture a screenshot at the given step index (0 = initial state, N = after step N).

    The screenshots list is treated as ordered by index — duplicate indices
    overwrite the existing entry rather than appending.
    """
    cfg = ConfigManager()
    if len(report.screenshots) >= cfg.sentinel.max_screenshots:
        return None
    path = DataInterface().screenshot_path(report.run_id, index)
    path.parent.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(path), full_page=False)
    ensure_screenshot_thumbnail(report.run_id, path.name)
    rel = f"screenshots/{path.name}"
    if rel not in report.screenshots:
        report.screenshots.append(rel)
    return rel


def ensure_screenshot_thumbnail(run_id: str, filename: str) -> Path | None:
    if not re.match(r"^step-\d{2}(?:-annot)?\.png$", filename):
        return None
    data = DataInterface()
    source_path = data.screenshots_dir(run_id) / filename
    thumb_path = data.screenshot_thumbnail_path(run_id, filename)
    if thumb_path.exists():
        return thumb_path
    if not source_path.exists():
        return None
    try:
        from PIL import Image
    except Exception as error:
        log_event(
            "sentinel", "sentinel.thumbnail_skipped",
            level=logging.WARNING, run_id=run_id, filename=filename,
            reason="pillow_unavailable", error_type=type(error).__name__,
        )
        return None

    try:
        max_px = ConfigManager().sentinel.screenshot_thumb_max_px
        with Image.open(source_path) as img:
            img.thumbnail((max_px, max_px), Image.Resampling.LANCZOS)
            thumb = img.copy()
        thumb_path.parent.mkdir(parents=True, exist_ok=True)
        thumb.save(thumb_path, format="PNG", optimize=True)
    except Exception as e:
        log_event(
            "sentinel", "sentinel.thumbnail_create_failed",
            level=logging.WARNING, run_id=run_id, filename=filename,
            exc_info=e, error_type=type(e).__name__,
        )
        return None
    return thumb_path


def _screenshot_image_paths(report: Report, screenshot: str | None) -> list[Path]:
    if not screenshot:
        return []
    filename = Path(screenshot).name
    path = DataInterface().screenshots_dir(report.run_id) / filename
    return [path] if path.exists() else []


def _capture_annotated_screenshot(report: Report, screenshot: str | None, observation: dict, index: int) -> str | None:
    if not screenshot:
        return None
    raw_filename = Path(screenshot).name
    raw_path = DataInterface().screenshots_dir(report.run_id) / raw_filename
    out_path = DataInterface().annotated_screenshot_path(report.run_id, index)
    written = _annotate_screenshot(
        raw_path,
        out_path,
        observation.get("elements") or [],
        observation.get("viewport"),
    )
    if written is None:
        return None
    ensure_screenshot_thumbnail(report.run_id, out_path.name)
    rel = f"screenshots/{out_path.name}"
    if rel not in report.annotated_screenshots:
        report.annotated_screenshots.append(rel)
    return rel


def _annotated_image_paths(report: Report, annotated: str | None, raw: str | None) -> list[Path]:
    if annotated:
        path = DataInterface().screenshots_dir(report.run_id) / Path(annotated).name
        if path.exists():
            paths = [path]
            # If the prior step was 'peek', also attach the raw (un-annotated)
            # screenshot so the model can see ui obscured by annotation boxes.
            if report._peek_pending and raw:
                raw_path = DataInterface().screenshots_dir(report.run_id) / Path(raw).name
                if raw_path.exists():
                    paths.append(raw_path)
            return paths
    return _screenshot_image_paths(report, raw)


_ANNOTATION_PALETTE = [
    (224, 122, 95),    # coral
    (135, 168, 120),   # sage
    (244, 162, 97),    # peach
    (233, 196, 106),   # gold
    (74, 93, 74),      # moss
    (107, 142, 90),    # sage-dark
]


def _annotate_screenshot(
    raw_path: Path,
    out_path: Path,
    elements: list[dict],
    viewport: dict | None,
) -> Path | None:
    if not raw_path.exists():
        return None
    try:
        from PIL import Image, ImageDraw, ImageFont
    except Exception as error:
        log_event(
            "sentinel", "sentinel.annotation_skipped",
            level=logging.WARNING, path=raw_path.name,
            reason="pillow_unavailable", error_type=type(error).__name__,
        )
        return None
    cfg = ConfigManager()
    try:
        img = Image.open(raw_path).convert("RGB")
    except Exception as e:
        log_event(
            "sentinel", "sentinel.screenshot_open_failed",
            level=logging.WARNING, path=raw_path.name,
            exc_info=e, error_type=type(e).__name__,
        )
        return None

    img_w, img_h = img.size
    vp_w = float((viewport or {}).get("w") or img_w)
    vp_h = float((viewport or {}).get("h") or img_h)
    sx = img_w / vp_w if vp_w else 1.0
    sy = img_h / vp_h if vp_h else 1.0

    draw = ImageDraw.Draw(img, "RGBA")
    try:
        font = ImageFont.truetype("DejaVuSans-Bold.ttf", cfg.sentinel.annotation_label_font_px)
    except Exception:
        font = ImageFont.load_default()

    box_w = max(1, int(cfg.sentinel.annotation_box_width_px))
    pad = max(1, int(cfg.sentinel.annotation_label_pad_px))

    for idx, el in enumerate(elements):
        rect = el.get("rect") or {}
        try:
            x = float(rect["x"]) * sx
            y = float(rect["y"]) * sy
            w = float(rect["w"]) * sx
            h = float(rect["h"]) * sy
        except (KeyError, TypeError, ValueError):
            continue
        if w <= 1 or h <= 1:
            continue
        x1, y1 = max(0, x), max(0, y)
        x2, y2 = min(img_w, x + w), min(img_h, y + h)
        if x2 <= x1 or y2 <= y1:
            continue

        color = _ANNOTATION_PALETTE[idx % len(_ANNOTATION_PALETTE)]
        draw.rectangle([x1, y1, x2, y2], outline=color + (255,), width=box_w)

        label = str(el.get("id") or "")
        if not label:
            continue
        tb = draw.textbbox((0, 0), label, font=font)
        text_w, text_h = tb[2] - tb[0], tb[3] - tb[1]

        lx2 = x1 + text_w + pad * 2
        ly2 = y1 + text_h + pad * 2
        draw.rectangle([x1, y1, lx2, ly2], fill=color + (230,))
        draw.text((x1 + pad, y1 + pad), label, fill=(255, 255, 255, 255), font=font)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        img.save(out_path, format="PNG")
    except Exception as e:
        log_event(
            "sentinel", "sentinel.annotation_save_failed",
            level=logging.WARNING, path=out_path.name,
            exc_info=e, error_type=type(e).__name__,
        )
        return None
    return out_path


def _observe_page(page) -> dict:
    cfg = ConfigManager()
    return page.evaluate(
        """
        ({ maxElements, maxTextChars, maxElementTextChars, scrollTolerancePx }) => {
          const rectOf = (el) => {
            const r = el.getBoundingClientRect();
            return {x: r.x, y: r.y, w: r.width, h: r.height};
          };
          const SELECTOR = (
            'a,button,input,textarea,select,'
            + '[role="button"],[role="link"],[role="menuitem"],[role="tab"],[role="checkbox"],[role="radio"],'
            + '[onclick],[tabindex]:not([tabindex="-1"])'
          );
          // Walk a root (document or shadowRoot) and collect candidates that
          // match SELECTOR, descending into any open shadow roots we find.
          // Closed shadow roots are unreachable from JS and silently skipped.
          const walk = (root, out, hosts) => {
            for (const el of root.querySelectorAll(SELECTOR)) out.push(el);
            for (const el of root.querySelectorAll('*')) {
              if (el.shadowRoot) {
                hosts.set(el.shadowRoot, el);
                walk(el.shadowRoot, out, hosts);
              }
            }
          };
          // shadowRoot.elementFromPoint(x,y) descends one level; chain it so
          // the topmost element returned is the deepest visible one.
          const deepElementFromPoint = (x, y) => {
            let el = document.elementFromPoint(x, y);
            while (el && el.shadowRoot) {
              const inner = el.shadowRoot.elementFromPoint(x, y);
              if (!inner || inner === el) break;
              el = inner;
            }
            return el;
          };
          // Clear ids set by previous observations, including those stamped
          // inside open shadow roots.
          const clearRoot = (root) => {
            for (const el of root.querySelectorAll('[data-sentinel-id]')) el.removeAttribute('data-sentinel-id');
            for (const el of root.querySelectorAll('*')) if (el.shadowRoot) clearRoot(el.shadowRoot);
          };
          clearRoot(document);

          const usable = (el) => {
            const r = el.getBoundingClientRect();
            const style = window.getComputedStyle(el);
            if (r.width <= 0 || r.height <= 0) return false;
            if (r.bottom <= 0 || r.right <= 0 || r.top >= window.innerHeight || r.left >= window.innerWidth) return false;
            if (style.visibility === 'hidden' || style.display === 'none' || style.pointerEvents === 'none') return false;
            if (el.disabled || el.getAttribute('aria-hidden') === 'true' || el.closest('[aria-hidden="true"],[inert]')) return false;
            const cx = Math.min(Math.max(r.left + r.width / 2, 0), window.innerWidth - 1);
            const cy = Math.min(Math.max(r.top + r.height / 2, 0), window.innerHeight - 1);
            const top = deepElementFromPoint(cx, cy);
            return Boolean(top && (el === top || el.contains(top) || (top.getRootNode && top.getRootNode().host && el.contains(top.getRootNode().host))));
          };

          const candidates = [];
          const hosts = new Map();  // shadowRoot -> host element (currently unused but reserved)
          walk(document, candidates, hosts);
          const elements = [];
          for (const el of candidates) {
            if (elements.length >= maxElements) break;
            if (!usable(el)) continue;
            const id = `e${elements.length + 1}`;
            el.setAttribute('data-sentinel-id', id);
            elements.push({
              id,
              tag: el.tagName.toLowerCase(),
              type: el.getAttribute('type') || '',
              text: (el.innerText || el.value || el.getAttribute('aria-label') || el.getAttribute('placeholder') || el.href || '').trim().slice(0, maxElementTextChars),
              href: el.href || '',
              rect: rectOf(el)
            });
          }
          const bodyText = (document.body ? document.body.innerText : '').replace(/\\s+/g, ' ').trim().slice(0, maxTextChars);
          const scrollingEl = document.scrollingElement || document.documentElement;
          const scrollY = Math.max(0, window.scrollY || scrollingEl.scrollTop || 0);
          const scrollHeight = Math.max(
            scrollingEl.scrollHeight || 0,
            document.documentElement ? document.documentElement.scrollHeight : 0,
            document.body ? document.body.scrollHeight : 0
          );
          const screenHeight = window.innerHeight || scrollingEl.clientHeight || 0;
          const maxY = Math.max(0, scrollHeight - screenHeight);
          const canScrollDown = scrollY < maxY - scrollTolerancePx;
          return {
            url: location.href,
            title: document.title,
            text: bodyText,
            elements,
            viewport: {w: window.innerWidth, h: window.innerHeight},
            scroll: {
              y: Math.round(scrollY),
              max_y: Math.round(maxY),
              can_scroll_down: canScrollDown,
              can_scroll_up: scrollY > scrollTolerancePx,
              at_bottom: !canScrollDown
            }
          };
        }
        """,
        {
            "maxElements": cfg.sentinel.observation_max_elements,
            "maxTextChars": cfg.sentinel.observation_text_max_chars,
            "maxElementTextChars": cfg.sentinel.observation_element_text_max_chars,
            "scrollTolerancePx": cfg.sentinel.scroll_position_tolerance_px,
        },
    )


def _agent_prompt(report: Report, observation: dict) -> str:
    history = [
        {"action": step.action, "reason": step.reason, "result": step.result.model_dump(exclude_none=True)}
        for step in report.steps[-6:]
    ]
    elements = [
        {
            "id": el.get("id", ""),
            "tag": el.get("tag", ""),
            "type": el.get("type", ""),
            "label": el.get("text", ""),
        }
        for el in (observation.get("elements") or [])
    ]
    hints = [
        f.detail
        for f in report.findings
        if f.severity in {"warning", "error"} and f.title == "Repeated click with no navigation"
    ][-1:]
    payload = {
        "target_url": report.target_url,
        "user_prompt": report.prompt or "Explore and test the site's main unauthenticated flows.",
        "history": history,
        "page": {
            "url": observation.get("url", ""),
            "title": observation.get("title", ""),
            "elements": elements,
            "scroll": observation.get("scroll") or {},
        },
        "additional_domains": list(report.additional_domains or []),
        "instructions": (
            "The attached screenshot shows the page with each interactive element outlined and "
            "labelled with a synthetic id (e.g. e1, e2). Use the screenshot as your primary input "
            "and choose elements visually. The 'elements' list is only a key for resolving labels "
            "to ids; do not rely on it for spatial layout. If additional_domains is non-empty, "
            "external navigation is permitted only to those domains; other external domains are blocked. "
            "If the annotation boxes are obscuring text you need to read, or you think a clickable "
            "element is missing from the elements list, use the peek action — the next step will "
            "include a clean un-annotated copy of the same screenshot. When the user asks you to "
            "check all items, apps, links, cards, rows, or sections on a page, use scroll to inspect "
            "below the current screen before finishing. Do not assume the visible screen is the "
            "whole page."
        ),
    }
    if hints:
        payload["hints"] = hints
    return json.dumps(payload, indent=2)


def _full_page_coverage_requested(prompt: str | None) -> bool:
    pattern = ConfigManager().sentinel.full_page_scope_prompt_pattern
    return bool(re.search(pattern, prompt or "", re.IGNORECASE))


def _finish_requires_more_scroll(report: Report, action: AgentAction, observation: dict) -> bool:
    if action.action != "finish" or not _full_page_coverage_requested(report.prompt):
        return False
    scroll = observation.get("scroll") or {}
    return bool(scroll.get("can_scroll_down"))


def _apply_action(
    page,
    action: AgentAction,
    target: ValidatedTarget,
    allow_external: bool = False,
    additional_domains: list[str] | None = None,
) -> dict:
    cfg = ConfigManager().sentinel
    try:
        if action.action == "finish":
            return {"ok": True, "url": page.url}
        if action.action == "peek":
            # No-op on the page; the runner re-attaches the raw screenshot to
            # the *next* agent call so the model can see ui obscured by the
            # annotation overlay.
            return {"ok": True, "url": page.url}
        if action.action == "wait":
            page.wait_for_timeout(cfg.wait_action_ms)
            return {"ok": True, "url": page.url}
        if action.action == "scroll":
            delta = -cfg.scroll_action_delta_px if (action.value or "").lower() == "up" else cfg.scroll_action_delta_px
            page.mouse.wheel(0, delta)
            page.wait_for_timeout(cfg.post_scroll_settle_ms)
            return {"ok": True, "url": page.url}
        if action.action == "goto":
            next_url = urljoin(page.url, action.url or target.url)
            return _goto_page(page, next_url, target, allow_external=allow_external, additional_domains=additional_domains)

        locator = page.locator(f'[data-sentinel-id="{action.element_id}"]').first
        if action.action == "click":
            blocked_url = _blocked_external_click_url(page, locator, target, allow_external, additional_domains)
            if blocked_url:
                return {
                    "ok": False,
                    "error": "Navigation outside target host blocked",
                    "url": page.url,
                    "blocked_url": blocked_url,
                }
            locator.click()
            try:
                page.wait_for_load_state(
                    "domcontentloaded",
                    timeout=cfg.post_click_load_timeout_ms,
                )
            except Exception:
                pass
            page.wait_for_timeout(cfg.post_click_settle_ms)
        elif action.action == "fill":
            locator.fill(action.value or "test")
            page.wait_for_timeout(cfg.post_fill_settle_ms)
        elif action.action == "select":
            locator.select_option(label=action.value or "")
            page.wait_for_timeout(cfg.post_select_settle_ms)
        return {"ok": True, "url": page.url}
    except Exception as e:
        return {"ok": False, "error": str(e), "url": getattr(page, "url", "")}


def _blocked_external_click_url(
    page,
    locator,
    target: ValidatedTarget,
    allow_external: bool,
    additional_domains: list[str] | None = None,
) -> str:
    if allow_external:
        return ""
    href = locator.evaluate(
        """
        (el) => {
          const anchor = el.closest ? el.closest('a[href]') : null;
          if (anchor) return anchor.href || '';
          const form = el.closest ? el.closest('form[action]') : null;
          if (form) return form.action || '';
          return '';
        }
        """
    )
    parsed = urlparse(urljoin(page.url, str(href or "")))
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return ""
    hostname = parsed.hostname.rstrip(".").lower()
    if _navigation_host_allowed(hostname, target.hostname, additional_domains):
        return ""
    return parsed.geturl()


def _record_step(report: Report, action: str, reason: str, result: dict) -> None:
    report.steps.append(
        Step(
            index=len(report.steps) + 1,
            action=action,
            reason=reason,
            result=ActionResult.model_validate(result),
            created_at=utc_now_iso(),
        )
    )


def _request_agent_action(report, observation, image_paths, allow_external, known_ids):
    """Ask the LLM for the next action, retrying once on a parse failure.

    Returns the parsed AgentAction, or None if the run should abort. On abort,
    an ``invalid`` step has already been recorded and a finding added.
    """
    cfg = ConfigManager().sentinel
    attempts = max(1, cfg.agent_parse_retry_attempts + 1)
    last_error = None
    last_text = ""
    for attempt in range(attempts):
        agent_text = _get_provider().agent_text(
            _agent_prompt(report, observation),
            image_paths=image_paths,
            allow_accounts=bool(report.allow_accounts),
            demographic=str(report.demographic or ""),
            allow_external=allow_external or bool(report.additional_domains),
            card_details=(report.card_details.model_dump() if report.card_details else None),
            account_credentials=(
                report.account_credentials.model_dump() if report.account_credentials else None
            ),
        )
        try:
            return parse_agent_action(agent_text, known_ids)
        except ActionValidationError as e:
            last_error = e
            last_text = agent_text
            if attempt + 1 < attempts:
                log_event(
                    "sentinel", "sentinel.agent_parse_retry",
                    level=logging.WARNING, user=report.owner or None,
                    run_id=report.run_id, attempt=attempt + 1,
                    reason="invalid_agent_output",
                    error_type=type(e).__name__,
                )
    _record_step(report, "invalid", str(last_error), {"agent_text": last_text})
    _add_finding(report, "warning", "Agent response unparseable", str(last_error))
    return None


_LOGIN_FAIL_PREFIX = "login failed:"


def _detect_login_failure(report: Report) -> str:
    """If the agent finished with a 'login failed:' marker, return the reason."""
    if not report.allow_accounts or not report.steps:
        return ""
    last = report.steps[-1]
    if last.action != "finish":
        return ""
    reason = str(last.reason or "").strip()
    if reason.lower().startswith(_LOGIN_FAIL_PREFIX):
        return reason
    return ""


def _detect_click_loop(report: Report) -> bool:
    """Surface a finding when the agent clicks the same element_id repeatedly
    without the URL changing — usually a sign the target control is broken or
    leads back to the same page.

    Returns True when the run should be stopped (warning count has exceeded
    ``click_loop_max_warnings``).
    """
    cfg = ConfigManager().sentinel
    threshold = cfg.click_loop_threshold
    if threshold <= 0:
        return False
    steps = report.steps
    if len(steps) < threshold:
        return False
    tail = steps[-threshold:]
    if not all(s.action == "click" for s in tail):
        return False
    urls = {s.result.url for s in tail}
    if len(urls) != 1:
        return False
    reasons = {(s.reason or "")[:60] for s in tail}
    last_step = tail[-1].index
    # Suppress if we already flagged a loop ending at this same step count.
    for finding in report.findings:
        if finding.title == "Repeated click with no navigation" and str(last_step) in finding.detail:
            return False
    _add_finding(
        report,
        "warning",
        "Repeated click with no navigation",
        f"The agent clicked through {threshold} consecutive steps ending at step {last_step} on URL {next(iter(urls))} "
        f"without the page changing. Reasons seen: {sorted(reasons)}. The control may be broken or self-referential; "
        "try a different element.",
    )
    loop_warnings = sum(
        1 for f in report.findings if f.title == "Repeated click with no navigation"
    )
    return cfg.click_loop_max_warnings > 0 and loop_warnings >= cfg.click_loop_max_warnings
