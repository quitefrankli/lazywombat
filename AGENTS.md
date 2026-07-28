* Minimise token usage - this directly affects cost and speed:

* One tool call, not three: Prefer a single well-constructed command over multiple incremental checks.

* Git commits: only commit code when explicitly told to. Commit messages must be descriptive; small changes can use a one-line message, but larger changes need multiple lines or a short paragraph explaining what changed and why. When making a commit, use the dedicated commit agent: Claude Code via the `commit` subagent (`.claude/agents/commit.md`), Codex via the `commit` agent (`.codex/agents/commit.toml`).

* Context preservation: Background tasks return completion notifications with `<result>` tags containing only the final message. Do NOT call `TaskOutput` to check results. `TaskOutput` returns the full conversation transcript (every tool call, file read, and intermediate message), which wastes massive amounts of context. Wait for each task's completion notification and use the `<result>` tag content directly.

* Minimise comments/docstrings, only add comments when they are truly helpful i.e. deep functions, surprsing logic. Don't add any readme.md files or other documentation files unless explicitly asked to.

* Do test driven development, when unit/integration tests are appropriate, first write the test that defines the expected behavior, then implement the code to pass the test.

* DO NOT write superfluous tests (ie. constructors), prefer fewer but higher quality tests, the more end-2-end the better

* Constants belong in `config.py`: any named constant (limits, counts, timeouts, feature flags, model names, etc.) must be defined as an attribute of `ConfigManager` in `web_app/config.py`, not hardcoded at call sites.

* A debug server is usually available for debugging at 127.0.0.1:12345 its data can be found under ~/.nabicat_debug/...

* Production resource safety: when working on the production server, do not start a separate debug/test web app. Reuse existing diagnostics, keep tests and investigative commands narrowly scoped, and avoid full test suites, large generated media/transcodes, load tests, or other resource-intensive work unless explicitly requested.

* Project Architecture:
    - this project contains a collection of smaller subapps/subpages under web_app/ all of which share a similar ui/ux theme and share the same domain and host
    - Each subapp is a Flask Blueprint with its own templates/ and static/ folders
    - CSS and JS must live in the subapp's static/ folder (e.g. `metrics/static/style.css`), never inline in HTML templates
    - Link them via `{% block scripts %}` using `url_for('.static', filename='...')`

* Cross-browser / cross-device UI: this is a web app served to real users on a mix of browsers (Chrome, Firefox, Safari — including iOS Safari) and devices (desktop, tablet, phone). When making UI/CSS/JS changes:
    - Design mobile-first and verify layouts still work on narrow viewports (~375px wide phones). Use responsive units and existing `@media (max-width: 768px)` breakpoints rather than fixed pixel sizing.
    - Avoid WebKit-only / Chrome-only CSS without fallbacks. Be cautious with newer APIs (e.g. `:has()`, container queries, View Transitions) and check baseline support before relying on them.
    - Account for iOS Safari quirks: safe-area insets (`env(safe-area-inset-bottom)`), the dynamic bottom chrome hiding fixed elements, 100vh != viewport height, and touch vs. hover (`:hover` doesn't apply on touch — don't hide critical UI behind hover).
    - Touch targets should be large enough to tap (~40px min). Don't rely on hover-only tooltips for essential info.
    - Test behavior when the keyboard is open on mobile (fixed bottom bars can get covered).
    - Thumbnail/image grids should not assign all real image URLs directly in HTML. Use the established file_store/Hammock pattern: render a tiny placeholder `src`, put the real URL in a data attribute, lazy-load with `IntersectionObserver`, serialize requests with a small stagger, and retry failed loads with cache-busting query params. Define stagger/retry constants in `ConfigManager`.

* start every new session with "AGENTS.md read!"

## Concurrency & multi-worker (Redis)

The app runs under gunicorn with multiple sync worker **processes** (`-w`, set via `WORKERS` in `update_server.sh`, default 4). Each worker is a separate OS process, so anything that must be shared across requests lives in **Redis**, not module-level globals. Redis is a hard runtime dependency (`redis_url` in `ConfigManager`, default `redis://127.0.0.1:6379/0`); `update_server.sh` installs/enables `redis-server`, and `ensure_local_redis()` auto-starts one for local `python -m web_app` runs.

- **`web_app/redis_client.py`** is the hub: `get_redis()` (process-cached client), `run_once(job_id)` (scheduler decorator — each APScheduler job fires in every worker but a Redis `SET NX EX` ensures the body runs once), and `rmw_lock(name)` (the distributed mutex).
- **Rate limiter** (`helpers.py`) and **ephemeral RSA handshake keys** are Redis-backed so limits and the handshake work across workers. Sessions are signed cookies (stateless — fine). **Sentinel cancel flags** and **Tubio download progress** are Redis keys, not in-process dicts.

### rmw_lock + edit_model (the read-modify-write pattern)

JSON data files are read → mutated → written back within a request. Two workers doing this concurrently would clobber each other (last-write-wins). The fix is a **path-keyed distributed lock** that wraps the whole load→mutate→save span:

- **`rmw_lock(name)`** (`redis_client.py`) — a context manager implemented with `SET NX EX` + a token-checked release (works on real Redis *and* fakeredis, which has no Lua scripting). It is **reentrant per-thread** (a request is one thread), so a caller can wrap a span whose inner save re-locks the same name without deadlocking. Hold times are bounded by `rmw_lock_timeout_s` (auto-expire if a holder crashes) and `rmw_lock_blocking_timeout_s` (raise rather than hang forever). **Never hold the lock across slow I/O** (uploads, ffmpeg transcodes) — do the heavy work first, then lock only the metadata mutation.
- **`DataInterface.edit_model(path, Model)`** (`data_interface.py`) is the preferred API and wraps `rmw_lock` for you: it derives the lock name from the file path, loads the model *inside* the lock, yields it for mutation, and saves on clean exit — **only if the serialized model actually changed** (no-op edits skip the write). An exception in the block discards the mutation. This bundles the three things manual locking gets wrong: forgetting to lock, locking the save but not the read, and inconsistent lock names.
- Every subapp exposes a typed thin wrapper over `edit_model`: `edit_users` (account), `edit_goals` (todoist), `edit_data` (metrics), `edit_metadata` (tubio + file_store), `edit_meta` (hammock). Use these for **all writes**; use the plain `load_*`/`get_*` methods only for read-only paths. Do **not** call the bare `save_*` methods for read-modify-write.
- **Gotcha — no nested `edit_*` on the same file**: a nested call re-loads from disk and would miss the outer block's uncommitted mutations. Mutate the already-yielded model directly instead of calling another `edit_*`/`write_*` inside the block.
- On-disk formats are preserved by Pydantic field aliases + `serialize_by_alias=True` (e.g. `User.id` ↔ `username`, `PostMeta.template_data` ↔ `template-data`), so no data migration was needed.

**Single-worker-only feature:** the **dev terminal** (`web_app/dev/terminal.py`) holds live PTY subprocesses in a module-level `_sessions` dict — these are file descriptors that cannot move to Redis. Under multiple workers a terminal request can land on a worker that doesn't hold the session. It's an admin-only debug tool; if it must work reliably under `-w >1`, add nginx sticky-session affinity, otherwise run a single worker when using it.

## Sentinel subapp

- Admin-only blueprint (`web_app/sentinel/__init__.py`): every route is gated by `current_user.is_admin`.
- Run data lives at `~/.nabicat/data/sentinel/runs/<run_id>/`: `report.json` plus `screenshots/step-NN.png` (raw viewport capture) and `screenshots/step-NN-annot.png` (numbered-box overlay produced by `_annotate_screenshot`). Older runs are pruned to `max_retained_runs` on completion.
- Agent input model is **Set-of-Mark style**: each step the LLM receives the *annotated* PNG plus a slim `{id, tag, type, label}` map — no full DOM, no body text. `_observe_page` stamps `data-sentinel-id="eN"` on each visible interactive element so `_apply_action` can drive Playwright deterministically.
- Run config flows through `start_run(...)` and is stored on the report dict: `title`, `owner` (= `current_user.id`), `device`, `demographic`, `allow_accounts`, `allow_external`, `limit_s`. Form fields round-trip via `?url=&prompt=&...` query params (the Rerun button builds this URL).
- Device profiles map friendly keys to Playwright's `playwright.devices[...]` registry (`config.py SentinelConfig.device_profiles`); demographic prepends a persona sentence to the agent system prompt (`demographic_personas`).
- Off-host navigation is gated at the network layer via Playwright's `page.route("**/*", guard_route)` unless `allow_external=True`. The system prompt also tells the agent to stay on-site (TODO comment in `runner.py` notes this could be relaxed).
- PDF export (`/sentinel/report/<run_id>/pdf`) reuses headless Chromium via `render_report_pdf` in `runner.py` — Playwright is already a dep, no extra libs. Inline screenshots are embedded as base64 data URIs (`_render_final_report_for_pdf`) because `page.set_content` runs at `about:blank` which blocks `file://` subresource loads.
- Cancel uses a per-run `threading.Event` (`_cancel_events`); the run loop checks `_is_cancelled` between steps. UI shows a "cancelling" state immediately before the server confirms.
- Layout: `_sentinel_sidebar.html` is a shared partial fed by `sidebar_runs` from the blueprint context processor; both index and report pages render it. The sidebar is resizable (drag `#sentinel-sidebar-resizer`, persisted in `localStorage` as `sentinel.sidebar.width`).

## Hammock subapp

- Posts live on the filesystem: `~/.nabicat/data/hammock/projects/<project>/<post>/`. Each post has a `meta.json` with `template` (`markdown`/`gallery`/`raw`), `owner` (username), `title`, `date`.
- Authorization: post owner OR admin can edit/delete. Legacy posts (no `owner` field) are admin-only.
- Templated posts store source-of-truth alongside the rendered `index.html`:
    - `markdown` → `source.md`
    - `gallery` → `gallery.json` (`title`, `description`, `images`); originals in the post dir, WebP thumbnails in `thumbs/<filename>.webp`
- `DataInterface.get_post_content` re-renders templated posts on every view, so renderer/style changes propagate without re-saving existing posts.
- Per-user storage quotas: `hammock_non_admin_quota_bytes`, `hammock_admin_quota_bytes`; thumb size: `hammock_gallery_thumb_max_px`.

## UI/UX Design System — Honeydew Theme

All UI work must stay consistent with the established design system in `web_app/static/style.css`.

### Design Tokens (CSS variables — always use these, never hardcode values)

**Colors:**
- `--hw-bg-primary`: #F0FFF0 | `--hw-bg-secondary`: #FAF9F6 | `--hw-bg-cream`: #F5F5DC
- `--hw-sage`: #87A878 | `--hw-sage-light`: #A8C686 | `--hw-sage-dark`: #6B8E5A
- `--hw-forest`: #2D4A3E | `--hw-moss`: #4A5D4A
- `--hw-peach`: #F4A261 | `--hw-gold`: #E9C46A | `--hw-coral`: #E07A5F | `--hw-terracotta`: #D4866A
- `--hw-text-primary`: #2D4A3E | `--hw-text-secondary`: #5A6B5A | `--hw-text-muted`: #8A9A8A
- `--hw-border`: rgba(135, 168, 120, 0.2)

**Gradients:** `--hw-gradient-warm`, `--hw-gradient-sage`, `--hw-gradient-golden`, `--hw-gradient-soft`

**Shadows:** `--hw-shadow-sm` / `--hw-shadow-md` / `--hw-shadow-lg` / `--hw-shadow-glow`

**Border radius:** `--hw-radius-sm` 8px | `--hw-radius-md` 12px | `--hw-radius-lg` 16px | `--hw-radius-xl` 24px | `--hw-radius-full` 9999px

**Transitions:** `--hw-transition-fast` 0.2s | `--hw-transition-base` 0.3s | `--hw-transition-slow` 0.5s — all use `cubic-bezier(0.4, 0, 0.2, 1)`

### Typography
- Body: `'Nunito'` (400/500/600/700), sans-serif
- Headings: `'Playfair Display'` (600/700), serif
- Code/mono: `'SF Mono'`, monospace
- Both loaded from Google Fonts in `root_base.html`

### Component Conventions
- **Buttons:** `--hw-radius-md`, padding `0.625rem 1.25rem`, gradient fills, `translateY(-2px)` on hover
- **Cards:** `rgba(255,255,255,0.9)`, `--hw-radius-lg` (16px), `--hw-shadow-md`, `translateY(-4px)` on hover
- **Forms:** `rgba(255,255,255,0.8)` bg, 2px sage border, `--hw-radius-md`, focus ring `rgba(135,168,120,0.15)`
- **Navbar:** glassmorphism — `rgba(255,255,255,0.7)` + `backdrop-filter: blur(12px)`, `--hw-radius-lg`
- **Dropdowns/Modals:** `rgba(255,255,255,0.98)` + `backdrop-filter: blur(12px)`, `--hw-radius-md`
- **Empty states:** centered icon (4rem, 0.3 opacity), muted text, vertical flex

### Animation Patterns
- Hover lift: buttons `translateY(-2px)`, cards `translateY(-4px)`
- Entry: `@keyframes fadeInUp` — opacity 0→1 + translateY(20px)→0, 0.5s
- Pulse: `@keyframes pulse-glow` — box-shadow expansion, 2s infinite
- All interactive element transitions: `var(--hw-transition-fast)` or `var(--hw-transition-base)`

### Template Hierarchy
`root_base.html` → `subpage_base.html` → per-subapp base (e.g. `metrics_base.html`) → page templates

### Utility Classes
`.text-sage`, `.text-forest`, `.text-peach`, `.bg-honeydew`, `.bg-sage`, `.border-sage`, `.shadow-soft`, `.rounded-xl`
