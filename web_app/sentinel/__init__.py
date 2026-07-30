from __future__ import annotations

import base64
import logging
import re
import uuid

from flask import Blueprint, Response, abort, jsonify, redirect, render_template, request, send_from_directory, url_for
from flask_login import current_user, login_required
from markdown_it import MarkdownIt
from markupsafe import Markup

from web_app.config import ConfigManager
from web_app.helpers import redirect_with_access_denied
from web_app.logging_utils import log_event
from web_app.sentinel.data_interface import DataInterface
from web_app.sentinel.models import Report
from web_app.sentinel.runner import (
    delete_run,
    ensure_screenshot_thumbnail,
    get_run,
    _generate_title,
    render_report_pdf,
    request_cancel,
    start_run,
)
from web_app.sentinel.target_policy import TargetValidationError, validate_public_web_url


_SCREENSHOT_FILENAME_RE = re.compile(r"^step-\d{2}(?:-annot)?\.png$")


def _detect_account_keyword(prompt: str, keywords) -> str:
    text = prompt.lower()
    for kw in keywords:
        if re.search(rf"(?<!\w){re.escape(kw.lower())}(?!\w)", text):
            return kw
    return ""


def _validate_account_credentials(raw) -> dict | None:
    """Returns a dict {username, password, extras: {field: value}} or None.

    Treats an entirely-blank submission as None. If username or password is
    given without the other, raises so the user notices.
    """
    if not isinstance(raw, dict):
        return None
    username = str(raw.get("username", "")).strip()
    password = str(raw.get("password", ""))
    raw_extras = raw.get("extras") or {}
    if not isinstance(raw_extras, dict):
        raise ValueError("Account credentials extras must be a JSON object.")
    extras = {}
    for key, value in raw_extras.items():
        clean_key = str(key).strip()
        if not clean_key:
            continue
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9 _-]{0,39}", clean_key):
            raise ValueError(
                f"Account credential field name {clean_key!r} is invalid. Use letters, numbers, spaces, "
                "hyphens, or underscores; max 40 chars; must start with a letter."
            )
        extras[clean_key] = str(value)
    if not username and not password and not extras:
        return None
    if bool(username) ^ bool(password):
        raise ValueError("Provide both username and password, or leave both blank.")
    return {"username": username, "password": password, "extras": extras}


def _validate_card_details(payload) -> dict:
    raw_number = str(payload.get("card_number", ""))
    raw_expiry = str(payload.get("card_expiry", "")).strip()
    raw_cvv = str(payload.get("card_cvv", ""))
    digits = "".join(ch for ch in raw_number if ch.isdigit())
    if not 13 <= len(digits) <= 19:
        raise ValueError("Card number must be 13-19 digits.")
    if not re.fullmatch(r"\d{2}\s*/\s*\d{2}", raw_expiry):
        raise ValueError("Expiry must be in MM/YY format.")
    cvv_digits = "".join(ch for ch in raw_cvv if ch.isdigit())
    if not 3 <= len(cvv_digits) <= 4:
        raise ValueError("CVV must be 3 or 4 digits.")
    mm, yy = [part.strip() for part in raw_expiry.split("/")]
    return {
        "card_number": digits,
        "expiry": f"{mm}/{yy}",
        "cvv": cvv_digits,
    }


sentinel_api = Blueprint(
    "sentinel",
    __name__,
    template_folder="templates",
    static_folder="static",
    url_prefix="/sentinel",
)


@sentinel_api.before_request
@login_required
def require_elevated():
    config = ConfigManager()
    if not current_user.has_elevated_access():
        return redirect_with_access_denied(
            config.elevated_access_denied_message,
            config.sentinel_access_denied_api_prefixes,
        )


def _derive_batches(reports: list[Report], max_n: int | None = None) -> list[dict]:
    """Group runs by batch_id into batch summaries, newest-first.

    A batch is no longer a saved entity — it is just the set of runs that share
    a batch_id. ``reports`` is assumed newest-first (as list_reports returns),
    so first appearance of each batch_id preserves recency order.
    """
    groups: dict[str, dict] = {}
    for run in reports:
        bid = run.batch_id
        if not bid:
            continue
        group = groups.get(bid)
        if group is None:
            groups[bid] = {
                "batch_id": bid,
                "name": run.batch_label or bid,
                "owner": run.owner,
                "created_at": run.created_at,
                "items": [run],
            }
        else:
            group["items"].append(run)
            # Earliest run in the group is the batch's creation time.
            if run.created_at and run.created_at < group["created_at"]:
                group["created_at"] = run.created_at
    derived = list(groups.values())
    return derived[:max_n] if max_n is not None else derived


@sentinel_api.context_processor
def inject_app_name():
    cfg = ConfigManager()
    data = DataInterface()
    reports = data.list_reports()[: cfg.sentinel.max_retained_runs]
    return dict(
        app_name="Sentinel",
        sidebar_runs=reports,
        sidebar_batches=_derive_batches(reports, cfg.sentinel.max_retained_batches),
    )


def _truthy(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _limit_from_request(raw_limit) -> int:
    cfg = ConfigManager()
    try:
        limit_mins = int(raw_limit) if raw_limit not in (None, "") else cfg.sentinel.default_limit_mins
    except (TypeError, ValueError):
        limit_mins = cfg.sentinel.default_limit_mins
    limit_mins = max(cfg.sentinel.min_limit_mins, min(limit_mins, cfg.sentinel.max_limit_mins))
    return limit_mins * 60


def _validate_additional_domains(raw) -> list[str]:
    if raw in (None, ""):
        return []
    if isinstance(raw, str):
        values = re.split(r"[\s,]+", raw)
    elif isinstance(raw, (list, tuple)):
        values = raw
    else:
        raise ValueError("Additional domains must be a newline, comma, or space separated list.")

    cfg = ConfigManager().sentinel
    domains = []
    seen = set()
    for value in values:
        item = str(value or "").strip()
        if not item:
            continue
        if len(item) > cfg.additional_domain_max_chars:
            raise ValueError("Additional domain is too long.")
        try:
            checked = validate_public_web_url(item)
        except TargetValidationError as e:
            raise ValueError(f"Additional domain {item!r} is invalid: {e}") from e
        hostname = checked.hostname.lower().rstrip(".")
        if hostname not in seen:
            seen.add(hostname)
            domains.append(hostname)
        if len(domains) > cfg.additional_domains_max_count:
            raise ValueError(f"Additional domains are limited to {cfg.additional_domains_max_count}.")
    return domains


def _screenshot_url(run_id: str, filename: str, thumbnail: bool = False) -> str:
    suffix = f"/thumb/{filename}" if thumbnail else f"/{filename}"
    return f"/sentinel/report/{run_id}/screenshots{suffix}"


def _resolve_screenshot_src(src: str, run_id: str, allowed_filenames: set[str], thumbnail: bool = False) -> str | None:
    filename = src.rsplit("/", 1)[-1]
    if filename not in allowed_filenames or not _SCREENSHOT_FILENAME_RE.match(filename):
        return None
    return _screenshot_url(run_id, filename, thumbnail=thumbnail)


_TRANSPARENT_GIF_DATA_URI = "data:image/gif;base64,R0lGODlhAQABAAAAACwAAAAAAQABAAA="


def _render_final_report(markdown_text: str, run_id: str, screenshots: list[str]) -> Markup:
    md = MarkdownIt("commonmark", {"html": False})
    allowed = {str(s).rsplit("/", 1)[-1] for s in screenshots or []}
    default_image = md.renderer.rules.get("image")

    def render_image(tokens, idx, options, env):
        token = tokens[idx]
        src = token.attrGet("src") or ""
        resolved = _resolve_screenshot_src(src, run_id, allowed, thumbnail=True)
        if resolved is None:
            return ""
        # Route final-report screenshots through the same staggered/retrying
        # loader the debug grid uses, so that on-demand thumbnail generation
        # races and innerHTML re-renders during polling don't leave images
        # permanently stuck. The actual src is set client-side once the
        # loader picks the img up.
        token.attrSet("src", _TRANSPARENT_GIF_DATA_URI)
        token.attrSet("data-screenshot-src", resolved)
        token.attrSet("data-full", _resolve_screenshot_src(src, run_id, allowed) or "")
        token.attrSet("loading", "lazy")
        token.attrSet("decoding", "async")
        existing_class = token.attrGet("class") or ""
        token.attrSet("class", (existing_class + " sentinel-final-report-img").strip())
        if default_image:
            return default_image(tokens, idx, options, env)
        return md.renderer.renderToken(tokens, idx, options)

    md.renderer.rules["image"] = render_image
    return Markup(md.render(markdown_text or ""))


def _render_final_report_for_pdf(markdown_text: str, run_id: str, screenshots: list[str]) -> Markup:
    """Like _render_final_report, but inline screenshot src is a base64 data: URI so headless
    Chromium can decode the image without a network or file:// fetch (the page origin from
    set_content is about:blank, which blocks file:// subresource loads)."""
    md = MarkdownIt("commonmark", {"html": False})
    allowed = {str(s).rsplit("/", 1)[-1] for s in screenshots or []}
    default_image = md.renderer.rules.get("image")
    screenshots_dir = DataInterface().screenshots_dir(run_id)

    def render_image(tokens, idx, options, env):
        token = tokens[idx]
        src = token.attrGet("src") or ""
        filename = src.rsplit("/", 1)[-1]
        if filename not in allowed or not _SCREENSHOT_FILENAME_RE.match(filename):
            return ""
        path = screenshots_dir / filename
        try:
            data = path.read_bytes()
        except OSError:
            return ""
        encoded = base64.b64encode(data).decode("ascii")
        token.attrSet("src", f"data:image/png;base64,{encoded}")
        existing_class = token.attrGet("class") or ""
        token.attrSet("class", (existing_class + " sentinel-final-report-img").strip())
        if default_image:
            return default_image(tokens, idx, options, env)
        return md.renderer.renderToken(tokens, idx, options)

    md.renderer.rules["image"] = render_image
    return Markup(md.render(markdown_text or ""))


def _report_payload(report: Report) -> dict:
    cfg = ConfigManager()
    payload = report.model_dump()
    payload["final_report_html"] = str(
        _render_final_report(report.final_report, report.run_id, list(report.screenshots or []))
    )
    payload["screenshot_load_stagger_ms"] = cfg.sentinel.screenshot_load_stagger_ms
    payload["screenshot_load_max_retries"] = cfg.sentinel.screenshot_load_max_retries
    payload["screenshot_load_retry_delay_ms"] = cfg.sentinel.screenshot_load_retry_delay_ms
    payload["device_label"] = cfg.sentinel.device_labels.get(report.device, "")
    payload["demographic_label"] = cfg.sentinel.demographic_labels.get(report.demographic, "")
    return payload


def _run_form_options() -> dict:
    """Shared select options + limit bounds for the run form and batch builder."""
    cfg = ConfigManager()
    return dict(
        device_options=[(key, cfg.sentinel.device_labels.get(key, key)) for key in cfg.sentinel.device_profiles],
        demographic_options=[
            (key, cfg.sentinel.demographic_labels.get(key, key)) for key in cfg.sentinel.demographic_personas
        ],
        region_options=[(key, label) for key, label in cfg.sentinel.region_labels.items()],
        default_device=cfg.sentinel.default_device,
        default_demographic=cfg.sentinel.default_demographic,
        default_region=cfg.sentinel.default_region,
        default_limit=cfg.sentinel.default_limit_mins,
        min_limit=cfg.sentinel.min_limit_mins,
        max_limit=cfg.sentinel.max_limit_mins,
        prompt_max_chars=cfg.sentinel.prompt_max_chars,
        title_max_chars=cfg.sentinel.title_max_chars,
        additional_domains_max_count=cfg.sentinel.additional_domains_max_count,
    )


def _validate_run_params(payload, *, with_credentials: bool) -> dict:
    """Validate a single run-param set and return kwargs for ``start_run``.

    Raises ValueError (or its subclass TargetValidationError) on any invalid
    input. When ``with_credentials`` is False, credential/card fields are
    ignored entirely — used to structurally validate saved batch items. When
    True, account/card details are validated and forwarded; they reach the run
    in memory and are only written to disk if the matching remember_* flag is on.
    """
    cfg = ConfigManager()
    raw_url = str(payload.get("url", "")).strip()
    prompt = str(payload.get("prompt", "")).strip()[: cfg.sentinel.prompt_max_chars]
    title = str(payload.get("title", "")).strip()[: cfg.sentinel.title_max_chars]
    allow_accounts = _truthy(payload.get("allow_accounts"))
    allow_external = _truthy(payload.get("allow_external"))
    allow_financial = _truthy(payload.get("allow_financial"))
    remember_account = _truthy(payload.get("remember_account"))
    remember_card = _truthy(payload.get("remember_card"))
    device = str(payload.get("device", "")).strip()
    demographic = str(payload.get("demographic", "")).strip()
    limit_s = _limit_from_request(payload.get("limit"))

    target = validate_public_web_url(raw_url)
    additional_domains = _validate_additional_domains(payload.get("additional_domains"))

    if not allow_accounts:
        hit = _detect_account_keyword(prompt, cfg.sentinel.account_keywords)
        if hit:
            raise ValueError(
                f'Prompt mentions "{hit}" but "Permit account creation, login, '
                'and deletion" is off. Enable it or rephrase the prompt.'
            )

    card_details = None
    account_credentials = None
    if with_credentials:
        if allow_financial:
            card_details = _validate_card_details(payload)
        if allow_accounts:
            account_credentials = _validate_account_credentials(payload.get("account_credentials"))

    return dict(
        target=target,
        prompt=prompt,
        limit_s=limit_s,
        title=title,
        allow_accounts=allow_accounts,
        allow_external=allow_external,
        additional_domains=additional_domains,
        allow_financial=allow_financial,
        card_details=card_details,
        account_credentials=account_credentials,
        remember_account=remember_account,
        remember_card=remember_card,
        device=device,
        demographic=demographic,
    )


def _run_form_context() -> dict:
    """Context for the single-run form. Field markup/options come from the
    shared run_fields() macro via `form_options`. A Rerun (?from=<run_id>) loads
    that run server-side and packs its fields — including the persisted test
    credentials/card — into a `prefill_item` blob that run_form.js applies."""
    cfg = ConfigManager()
    from_run = str(request.args.get("from", "")).strip()
    prefill_item = _run_prefill(from_run) if from_run else None
    return dict(
        runs=DataInterface().list_reports()[: cfg.sentinel.max_retained_runs],
        prefill_item=prefill_item,
        form_options=_run_form_options(),
    )


def _run_prefill(run_id: str) -> dict | None:
    """Reconstruct single-run form state from an existing run, for Rerun.
    Credentials are read from the persisted report (no longer passed via URL)."""
    report = get_run(run_id)
    if report is None:
        return None
    item = {
        "url": report.target_url,
        "prompt": report.prompt,
        "title": report.title,
        "limit": (report.limit_s or 0) // 60 or None,
        "allow_accounts": bool(report.allow_accounts),
        "allow_external": bool(report.allow_external),
        "allow_financial": bool(report.allow_financial),
        "additional_domains": "\n".join(report.additional_domains or []),
        "device": report.device,
        "demographic": report.demographic,
    }
    if report.account_credentials:
        item["account_credentials"] = report.account_credentials.model_dump()
        item["remember_account"] = bool(report.remember_account)
    if report.card_details:
        item["card_number"] = report.card_details.card_number
        item["card_expiry"] = report.card_details.expiry
        item["card_cvv"] = report.card_details.cvv
        item["remember_card"] = bool(report.remember_card)
    return item


@sentinel_api.route("/")
def index():
    if request.args:
        return redirect(url_for("sentinel.new_run", **request.args))
    cfg = ConfigManager()
    reports = DataInterface().list_reports()[: cfg.sentinel.max_retained_runs]
    batches = _derive_batches(reports, cfg.sentinel.max_retained_batches)
    active_statuses = {"queued", "running", "summarizing"}
    completed_statuses = {"completed", "timed_out", "cancelled", "failed"}
    stats = {
        "total_runs": len(reports),
        "active_runs": sum(1 for run in reports if run.status in active_statuses),
        "completed_runs": sum(1 for run in reports if run.status in completed_statuses),
        "batches": len(batches),
    }
    return render_template("sentinel_index.html", recent_runs=reports[:3], landing_stats=stats)


@sentinel_api.route("/pitch")
def pitch():
    return render_template("sentinel_pitch.html")


@sentinel_api.route("/run")
def new_run():
    return render_template("sentinel_run.html", **_run_form_context())


@sentinel_api.route("/api/runs", methods=["POST"])
def create_run():
    payload = request.get_json(silent=True) or request.form
    try:
        kwargs = _validate_run_params(payload, with_credentials=True)
    except ValueError as e:
        log_event(
            "sentinel",
            "sentinel.run_rejected",
            level=logging.WARNING,
            reason="invalid_parameters",
            error_type=type(e).__name__,
        )
        return jsonify({"error": str(e)}), 400
    result = start_run(
        kwargs.pop("target"),
        kwargs.pop("prompt"),
        kwargs.pop("limit_s"),
        owner=str(getattr(current_user, "id", "") or ""),
        **kwargs,
    )
    log_event(
        "sentinel",
        "sentinel.run_created",
        run_id=result["run_id"],
    )
    return jsonify(result), 202


@sentinel_api.route("/api/runs/<run_id>")
def run_status(run_id: str):
    report = get_run(run_id)
    if report is None:
        abort(404)
    return jsonify(_report_payload(report))


@sentinel_api.route("/api/runs/<run_id>/cancel", methods=["POST"])
def cancel(run_id: str):
    report = get_run(run_id)
    if report is None:
        abort(404)
    if report.status not in {"queued", "running", "summarizing"}:
        log_event(
            "sentinel",
            "sentinel.run_cancel_ignored",
            run_id=run_id,
            status=report.status,
        )
        return jsonify({"run_id": run_id, "status": report.status, "cancelled": False})
    cancelled = request_cancel(run_id)
    log_event(
        "sentinel",
        "sentinel.run_cancel_requested",
        level=logging.INFO if cancelled else logging.WARNING,
        run_id=run_id,
        cancelled=cancelled,
    )
    return jsonify({"run_id": run_id, "cancelled": cancelled})


@sentinel_api.route("/api/runs/<run_id>/delete", methods=["POST"])
def delete(run_id: str):
    report = get_run(run_id)
    if report is None:
        abort(404)
    if report.status in {"queued", "running", "summarizing"}:
        log_event(
            "sentinel",
            "sentinel.run_delete_rejected",
            level=logging.WARNING,
            run_id=run_id,
            reason="active",
            status=report.status,
        )
        return jsonify({"error": "Run is still active"}), 409
    if not delete_run(run_id):
        abort(404)
    log_event("sentinel", "sentinel.run_deleted", run_id=run_id)
    return jsonify({"run_id": run_id, "deleted": True})


# --- Batch jobs ------------------------------------------------------------

def _sanitize_batch_item(raw: dict) -> dict:
    """Validate a batch item structurally and return its normalized non-secret
    fields. Credentials/card data are handled separately by create_batch and
    forwarded to start_run in memory only."""
    if not isinstance(raw, dict):
        raise ValueError("Each batch item must be an object.")
    cfg = ConfigManager()
    # _validate_run_params raises on any invalid url/domain/prompt-keyword.
    validated = _validate_run_params(raw, with_credentials=False)
    target = validated["target"]
    label = str(raw.get("label", "")).strip()[: cfg.sentinel.title_max_chars]
    raw_region = str(raw.get("region", "")).strip()
    region = raw_region if raw_region in cfg.sentinel.region_labels else cfg.sentinel.default_region
    return {
        "label": label,
        "url": str(raw.get("url", "")).strip(),
        "_target_url": target.url,
        "_target_hostname": target.hostname,
        "prompt": str(raw.get("prompt", "")).strip()[: cfg.sentinel.prompt_max_chars],
        "title": str(raw.get("title", "")).strip()[: cfg.sentinel.title_max_chars],
        "device": str(raw.get("device", "")).strip(),
        "demographic": str(raw.get("demographic", "")).strip(),
        # region collected for parity with the single-run form; start_run does
        # not consume it yet (TODO: thread region through when region emulation
        # is wired up).
        "region": region,
        "limit_mins": _limit_from_request(raw.get("limit")) // 60,
        "allow_accounts": _truthy(raw.get("allow_accounts")),
        "allow_external": _truthy(raw.get("allow_external")),
        "allow_financial": _truthy(raw.get("allow_financial")),
        "additional_domains": _validate_additional_domains(raw.get("additional_domains")),
    }


def _generate_batch_name(items: list[dict]) -> str:
    cfg = ConfigManager()
    first = items[0] if items else {}
    name = _generate_title(
        target_url=first.get("_target_url") or first.get("url", ""),
        prompt=first.get("prompt", ""),
        target_hostname=first.get("_target_hostname", ""),
    )
    return str(name).strip()[: cfg.sentinel.batch_name_max_chars] or cfg.sentinel.batch_name_fallback


def _parse_batch_payload(payload) -> tuple[str, list[dict]]:
    """Returns (name, sanitized_items) or raises ValueError."""
    cfg = ConfigManager()
    name = str(payload.get("name", "")).strip()[: cfg.sentinel.batch_name_max_chars]
    raw_items = payload.get("items")
    if not isinstance(raw_items, list) or not raw_items:
        raise ValueError("A batch must contain at least one run.")
    if len(raw_items) > cfg.sentinel.max_batch_items:
        raise ValueError(f"A batch is limited to {cfg.sentinel.max_batch_items} runs.")
    items = [_sanitize_batch_item(item) for item in raw_items]
    if not name:
        name = _generate_batch_name(items)
    return name, items


def _batch_child_runs_by_id(batch_id: str) -> list[dict]:
    """All runs sharing this batch_id, as slim status dicts (newest-first)."""
    children = []
    for run in DataInterface().list_reports():
        if run.batch_id != batch_id:
            continue
        children.append({
            "run_id": run.run_id,
            "status": run.status,
            "run_outcome": run.run_outcome,
            "title": run.title or run.target_url,
            "batch_label": run.batch_label,
            "target_url": run.target_url,
        })
    return children


def _batch_summary(batch_id: str) -> dict | None:
    """Synthesize a batch header from the runs that share batch_id."""
    derived = _derive_batches(DataInterface().list_reports())
    for group in derived:
        if group["batch_id"] == batch_id:
            return group
    return None


def _batch_prefill(batch_id: str) -> dict | None:
    """Reconstruct builder form state (name + per-run items) from an existing
    batch's runs, so a "Rerun" reopens the builder ready to queue again. Returns
    None if no runs share this batch_id. Persisted credentials are reconstructed
    too so the rerun is ready without re-entry."""
    summary = _batch_summary(batch_id)
    if summary is None:
        return None
    # list_reports is newest-first; reverse so items keep the original order.
    items = []
    for run in reversed(summary["items"]):
        item = {
            "label": "",
            "url": run.target_url,
            "title": run.title,
            "prompt": run.prompt,
            "device": run.device,
            "demographic": run.demographic,
            "limit": (run.limit_s or 0) // 60 or None,
            "allow_accounts": bool(run.allow_accounts),
            "allow_external": bool(run.allow_external),
            "allow_financial": bool(run.allow_financial),
            "additional_domains": "\n".join(run.additional_domains or []),
        }
        if run.account_credentials:
            item["account_credentials"] = run.account_credentials.model_dump()
            item["remember_account"] = bool(run.remember_account)
        if run.card_details:
            item["card_number"] = run.card_details.card_number
            item["card_expiry"] = run.card_details.expiry
            item["card_cvv"] = run.card_details.cvv
            item["remember_card"] = bool(run.remember_card)
        items.append(item)
    return {"name": summary["name"], "items": items}


@sentinel_api.route("/batches")
def batches_index():
    prefill = None
    from_batch = str(request.args.get("from", "")).strip()
    if from_batch:
        prefill = _batch_prefill(from_batch)
    return render_template(
        "sentinel_batches.html",
        max_batch_items=ConfigManager().sentinel.max_batch_items,
        batch_name_max_chars=ConfigManager().sentinel.batch_name_max_chars,
        prefill_batch=prefill,
        form_options=_run_form_options(),
    )


@sentinel_api.route("/api/batches", methods=["POST"])
def create_batch():
    """Validate the items and queue them all immediately as runs sharing a new
    batch_id. There is no saved batch entity — the group is re-derived from the
    runs themselves. Credentials travel inline on each item and are forwarded to
    start_run; they're written to a run's report only when its remember_* flag
    is set."""
    payload = request.get_json(silent=True) or request.form
    try:
        name, items = _parse_batch_payload(payload)
    except ValueError as e:
        log_event(
            "sentinel",
            "sentinel.batch_rejected",
            level=logging.WARNING,
            reason="invalid_batch",
            error_type=type(e).__name__,
        )
        return jsonify({"error": str(e)}), 400

    raw_items = payload.get("items") or []
    batch_id = uuid.uuid4().hex
    owner = str(getattr(current_user, "id", "") or "")
    run_ids = []
    for idx, item in enumerate(items):
        merged = dict(item)
        merged["limit"] = item.get("limit_mins")
        # Inline per-item credentials live on the raw payload item, not the
        # normalized one (_sanitize_batch_item drops them).
        raw = raw_items[idx] if idx < len(raw_items) and isinstance(raw_items[idx], dict) else {}
        for key in ("account_credentials", "card_number", "card_expiry", "card_cvv",
                    "remember_account", "remember_card"):
            if key in raw:
                merged[key] = raw[key]
        try:
            kwargs = _validate_run_params(merged, with_credentials=True)
        except ValueError as e:
            log_event(
                "sentinel",
                "sentinel.batch_rejected",
                level=logging.WARNING,
                item_index=idx,
                reason="invalid_item",
                error_type=type(e).__name__,
            )
            return jsonify({"error": f"Run {idx + 1}: {e}"}), 400
        result = start_run(
            kwargs.pop("target"),
            kwargs.pop("prompt"),
            kwargs.pop("limit_s"),
            owner=owner,
            batch_id=batch_id,
            batch_label=name,
            **kwargs,
        )
        run_ids.append(result["run_id"])

    log_event(
        "sentinel",
        "sentinel.batch_created",
        batch_id=batch_id,
        runs=len(run_ids),
    )
    return jsonify({"batch_id": batch_id, "run_ids": run_ids}), 202


@sentinel_api.route("/batch/<batch_id>")
def batch_detail(batch_id: str):
    summary = _batch_summary(batch_id)
    if summary is None:
        abort(404)
    return render_template(
        "sentinel_batch.html",
        batch=summary,
        child_runs=_batch_child_runs_by_id(batch_id),
    )


@sentinel_api.route("/api/batch/<batch_id>")
def batch_status(batch_id: str):
    children = _batch_child_runs_by_id(batch_id)
    if not children:
        abort(404)
    return jsonify({"batch_id": batch_id, "child_runs": children})


@sentinel_api.route("/api/batch/<batch_id>/delete", methods=["POST"])
def delete_batch(batch_id: str):
    children = _batch_child_runs_by_id(batch_id)
    if not children:
        abort(404)
    if any(run.get("status") in {"queued", "running", "summarizing"} for run in children):
        log_event(
            "sentinel",
            "sentinel.batch_delete_rejected",
            level=logging.WARNING,
            batch_id=batch_id,
            reason="active_runs",
        )
        return jsonify({"error": "Batch has active runs"}), 409
    run_ids = [str(run.get("run_id")) for run in children if run.get("run_id")]
    for run_id in run_ids:
        delete_run(run_id)
    log_event(
        "sentinel",
        "sentinel.batch_deleted",
        batch_id=batch_id,
        runs=len(run_ids),
    )
    return jsonify({"batch_id": batch_id, "deleted": True, "run_ids": run_ids})


@sentinel_api.route("/report/<run_id>")
def report(run_id: str):
    report_data = get_run(run_id)
    if report_data is None:
        abort(404)
    return render_template("sentinel_report.html", report=_report_payload(report_data))


@sentinel_api.route("/report/<run_id>/json")
def report_json(run_id: str):
    report_data = get_run(run_id)
    if report_data is None:
        abort(404)
    return jsonify(_report_payload(report_data))


@sentinel_api.route("/report/<run_id>/pdf")
def report_pdf(run_id: str):
    report_data = get_run(run_id)
    if report_data is None:
        abort(404)
    if report_data.status not in {"completed", "timed_out", "cancelled", "failed"}:
        return jsonify({"error": "Run is still in progress"}), 409

    payload = report_data.model_dump()
    payload["final_report_html"] = str(
        _render_final_report_for_pdf(
            report_data.final_report, report_data.run_id, list(report_data.screenshots or [])
        )
    )
    cfg = ConfigManager()
    payload["device_label"] = cfg.sentinel.device_labels.get(report_data.device, "")
    payload["demographic_label"] = cfg.sentinel.demographic_labels.get(report_data.demographic, "")

    html = render_template("sentinel_report_pdf.html", report=payload)
    try:
        pdf_bytes = render_report_pdf(html)
    except Exception as e:
        log_event(
            "sentinel",
            "sentinel.report_pdf_failed",
            level=logging.ERROR,
            run_id=run_id,
            exc_info=e,
            error_type=type(e).__name__,
        )
        return jsonify({"error": f"PDF generation failed: {e}"}), 500

    safe_title = re.sub(r"[^A-Za-z0-9._-]+", "-", str(report_data.title or run_id)).strip("-")[:80] or run_id
    return Response(
        pdf_bytes,
        mimetype="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="sentinel-{safe_title}.pdf"'},
    )


@sentinel_api.route("/report/<run_id>/screenshots/<filename>")
def screenshot(run_id: str, filename: str):
    if not _SCREENSHOT_FILENAME_RE.match(filename):
        abort(404)
    try:
        directory = DataInterface().screenshots_dir(run_id)
    except ValueError:
        abort(404)
    return send_from_directory(directory, filename)


@sentinel_api.route("/report/<run_id>/screenshots/thumb/<filename>")
def screenshot_thumbnail(run_id: str, filename: str):
    if not _SCREENSHOT_FILENAME_RE.match(filename):
        abort(404)
    try:
        path = ensure_screenshot_thumbnail(run_id, filename)
        directory = DataInterface().screenshot_thumbnail_path(run_id, filename).parent
    except ValueError:
        abort(404)
    if path is None:
        abort(404)
    return send_from_directory(directory, filename)
