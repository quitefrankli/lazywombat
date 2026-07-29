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
