* Git commits: only commit code when explicitly told to. Commit messages must be descriptive; small changes can use a one-line message, but larger changes need multiple lines or a short paragraph explaining what changed and why. When making a commit, use the dedicated commit agent: Claude Code via the `commit` subagent (`.claude/agents/commit.md`), Codex via the `commit` agent (`.codex/agents/commit.toml`).

* Don't add any documentation files unless explicitly asked to.

* Do test driven development, when unit/integration tests are appropriate, first write the test that defines the expected behavior, then implement the code to pass the test.

* DO NOT write superfluous tests (ie. constructors), prefer fewer but higher quality tests, the more end-2-end the better

* Constants belong in `config.py`: any named constant (limits, counts, timeouts, feature flags, model names, etc.) must be defined as an attribute of `ConfigManager` in `web_app/config.py`, not hardcoded at call sites.

* A debug server is usually available for debugging at 127.0.0.1:12345 its data can be found under ~/.nabicat_debug/...

* Production resource safety: when working on the production server, do not start a separate debug/test web app. Reuse existing diagnostics, keep tests and investigative commands narrowly scoped, and avoid full test suites, large generated media/transcodes, load tests, or other resource-intensive work unless explicitly requested.

* Logging and auditability:
    - Emit application logs through `web_app.logging_utils.log_event`; do not call `logging.debug/info/warning/error/exception/critical/log` directly.
    - Every event must have a stable dotted event name and the owning subapp in `app`. `log_event` always includes `app`, `ip`, and `user` (nullable), and automatically adds the request correlation ID when called during a request.
    - Every state-changing route must log its outcome: success, handled rejection, and handled failure. Include stable resource IDs, status/reason codes, and useful counts, but do not log request bodies, credentials, tokens, cookies, card data, prompts, free-form user content, or other secrets.
    - When catching an exception instead of re-raising it, log the handled failure with `exc_info` and `error_type`. Background work must pass the initiating user when known and include its durable correlation key (for example `run_id`, `batch_id`, or `job_id`).
    - Generic request start/completion/exception events are provided centrally. Do not add redundant “request received” messages in individual routes; add semantic operation events that explain what the request actually changed.

* GitHub Actions intentionally does not install FFmpeg to keep CI fast. Mark every test that requires or invokes `ffmpeg` or `ffprobe` with `@pytest.mark.ffmpeg`; the CI workflow excludes these tests with `-m "not ffmpeg"`. Prefer mocked media commands for non-transcoding behavior where practical.

* Project Architecture:
    - this project contains a collection of smaller subapps/subpages under web_app/ all of which share a similar ui/ux theme and share the same domain and host
    - Each subapp is a Flask Blueprint with its own templates/ and static/ folders
    - Before changing a subapp, read and follow the app-specific instructions in that subapp's `README.md`
    - CSS and JS must live in the subapp's static/ folder (e.g. `metrics/static/style.css`), never inline in HTML templates
    - Link them via `{% block scripts %}` using `url_for('.static', filename='...')`

* Before changing UI/UX, read and follow the general instructions in `UIUX.md` as well as the relevant subapp's `README.md`.

* start every new session with "AGENTS.md read!"

## Concurrency & multi-worker (Redis)

The app runs under gunicorn with multiple sync worker **processes** (`-w`, set via `WORKERS` in `update_server.sh`, default 4). Each worker is a separate OS process, so anything that must be shared across requests lives in **Redis**, not module-level globals. Redis is a hard runtime dependency (`redis_url` in `ConfigManager`, default `redis://127.0.0.1:6379/0`); `update_server.sh` installs/enables `redis-server`, and `ensure_local_redis()` auto-starts one for local `python -m web_app` runs.

- **`web_app/redis_client.py`** is the hub: `get_redis()` (process-cached client), `run_once(job_id)` (scheduler decorator — each APScheduler job fires in every worker but a Redis `SET NX EX` ensures the body runs once), and `rmw_lock(name)` (the distributed mutex).
- **Rate limiter** (`helpers.py`) and **ephemeral RSA handshake keys** are Redis-backed so limits and the handshake work across workers. Sessions are signed cookies (stateless — fine). Subapp state that must cross worker boundaries must also use Redis.

### rmw_lock + edit_model (the read-modify-write pattern)

JSON data files are read → mutated → written back within a request. Two workers doing this concurrently would clobber each other (last-write-wins). The fix is a **path-keyed distributed lock** that wraps the whole load→mutate→save span:

- **`rmw_lock(name)`** (`redis_client.py`) — a context manager implemented with `SET NX EX` + a token-checked release (works on real Redis *and* fakeredis, which has no Lua scripting). It is **reentrant per-thread** (a request is one thread), so a caller can wrap a span whose inner save re-locks the same name without deadlocking. Hold times are bounded by `rmw_lock_timeout_s` (auto-expire if a holder crashes) and `rmw_lock_blocking_timeout_s` (raise rather than hang forever). **Never hold the lock across slow I/O** (uploads, ffmpeg transcodes) — do the heavy work first, then lock only the metadata mutation.
- **`DataInterface.edit_model(path, Model)`** (`data_interface.py`) is the preferred API and wraps `rmw_lock` for you: it derives the lock name from the file path, loads the model *inside* the lock, yields it for mutation, and saves on clean exit — **only if the serialized model actually changed** (no-op edits skip the write). An exception in the block discards the mutation. This bundles the three things manual locking gets wrong: forgetting to lock, locking the save but not the read, and inconsistent lock names.
- Every data-writing subapp exposes a typed thin wrapper over `edit_model`; see its README for the wrapper name. Use these for **all writes**; use plain load/get methods only for read-only paths. Do **not** call bare save methods for read-modify-write.
- **Gotcha — no nested `edit_*` on the same file**: a nested call re-loads from disk and would miss the outer block's uncommitted mutations. Mutate the already-yielded model directly instead of calling another `edit_*`/`write_*` inside the block.
- On-disk formats are preserved by Pydantic field aliases + `serialize_by_alias=True` (e.g. `User.id` ↔ `username`, `PostMeta.template_data` ↔ `template-data`), so no data migration was needed.
