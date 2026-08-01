# Sentinel

Admin-only Playwright-driven UX testing and reporting under `/sentinel`.

- Run data lives at `~/.nabicat/data/sentinel/runs/<run_id>/`: `report.json`, raw `screenshots/step-NN.png`, and annotated `screenshots/step-NN-annot.png`. Completed runs are pruned to `max_retained_runs`.
- The agent uses Set-of-Mark observations: an annotated PNG plus a slim `{id, tag, type, label}` map. `_observe_page` stamps visible controls with `data-sentinel-id`; `_apply_action` drives them deterministically.
- `start_run` persists `title`, `owner` (`current_user.id`), `device`, `demographic`, `allow_accounts`, `allow_external`, and `limit_s`. Rerun form fields round-trip through query parameters.
- Device profiles and demographic personas are defined on `SentinelConfig`.
- Unless external access is allowed, `page.route` blocks off-host requests at the network layer. The agent prompt also directs the agent to remain on-site.
- PDF export reuses Playwright Chromium. Screenshots are embedded as data URIs because `page.set_content` at `about:blank` cannot load `file://` resources.
- Cancel state is Redis-backed so cancellation works across gunicorn workers.
- The UI enters a cancelling state immediately, before the server confirms cancellation.
- `_sentinel_sidebar.html` is shared by index and report pages. Its drag-resized width is stored as `sentinel.sidebar.width` in local storage.

## Local agent QA

- `python -m web_app.sentinel run --target URL --prompt TEXT --json`
  runs the same Sentinel engine synchronously and returns a compact versioned JSON result.
- CLI exit codes distinguish pass (`0`), product failure (`1`), inconclusive or
  execution failure (`2`), and interruption/cancellation (`130`).
- Reports keep separate `lifecycle` and `verdict` fields. The legacy `status`
  field remains as the compatibility view consumed by the existing UI.
- The foreground CLI can target the usual local debug server without adding
  QA-specific options to the web-app launcher. The ordinary Sentinel UI still
  rejects local/private targets.
- Foreground runs persist progress and evidence in the normal
  `~/.nabicat/data/sentinel` store, so the ordinary Sentinel UI can observe
  agent-started reports without a separate report server.
