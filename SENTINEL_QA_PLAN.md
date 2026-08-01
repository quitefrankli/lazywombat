# Sentinel QA Automation Plan

## Goal

Enable a local Codex coding agent to run Sentinel against an isolated local
NabiCat server, receive a truthful machine-readable outcome, and inspect the
same persisted report and screenshots through a separate Sentinel UI server.

## Target workflow

1. Run the app under test on `127.0.0.1:12345` with isolated QA data.
2. Optionally run the Sentinel report UI on `127.0.0.1:12346`, without a
   reloader, against the Sentinel QA report store.
3. Invoke a foreground Sentinel CLI from Codex with a target URL, focused QA
   prompt, device, demographic, and time limit.
4. Persist progress and artifacts while the foreground run executes so the UI
   can observe it.
5. Return compact JSON and an exit code that distinguish a product failure
   from an inconclusive run or infrastructure failure.
6. Let a repo-scoped `$sentinel-qa` skill guide Codex through server health,
   scenario selection, CLI execution, result inspection, and one bounded rerun
   when reproducibility needs confirmation.

## Design constraints

- Keep the browser runner and report model as the single source of truth. The
  skill must remain a thin orchestration layer.
- Preserve the existing web UI and its asynchronous run behavior.
- Do not make a second machine HTTP API for the MVP; the foreground CLI is the
  reliable local agent contract.
- Keep Sentinel exploratory QA advisory. Deterministic unit/integration tests
  remain the primary correctness gate.
- Bind QA servers to loopback and isolate their data from `~/.nabicat` and the
  ordinary `~/.nabicat_debug` store.
- Do not conflate Flask debug/reloader behavior, local-target permission,
  automatic admin login, and QA data isolation.
- Preserve existing uncommitted edits in `web_app/config.py`,
  `web_app/sentinel/providers.py`, and `tests/unit/test_sentinel.py`.
- Add named constants and feature flags only through `ConfigManager`/
  `SentinelConfig`.
- Follow TDD for appropriate unit and integration behavior.
- Emit all application events with `log_event` and never log prompts,
  credentials, tokens, request bodies, or other free-form user content.

## Milestone 1: Truthful execution model

- Refactor run creation and execution so web requests can still launch a
  background thread while a CLI can execute the same run synchronously.
- Separate lifecycle state from the QA verdict:
  - lifecycle: queued, running, summarizing, finished, cancelled, timed out,
    or execution error;
  - verdict: pass, fail, or inconclusive.
- Preserve compatibility for existing persisted reports and current templates.
- Never classify a verdict-provider exception or malformed verdict as a pass;
  return `inconclusive` with a stable reason instead.
- Treat browser/provider failures as execution errors rather than product
  failures.
- Add an explicit abandoned/interrupted terminal outcome and recovery for
  stale reports that were left active by a dead process.
- Keep Redis-backed cancellation working for both web-started and foreground
  runs.

## Milestone 2: Foreground QA CLI

- Add a module entry point under `web_app.sentinel` with a `run` command.
- Accept at minimum: target URL, prompt, title, device, demographic, limit,
  optional account/external-navigation flags, and JSON output.
- Run synchronously, handle SIGINT/SIGTERM, persist the terminal state, and
  print only the final machine payload to stdout in JSON mode.
- Include: schema version, run ID, lifecycle, verdict, reason, target URL,
  report path, optional report URL, finding counts, duration, and tested git
  revision/working-tree state when available.
- Use exit codes:
  - `0`: pass;
  - `1`: product/QA fail;
  - `2`: inconclusive or execution/infrastructure failure;
  - `130`: cancelled/interrupted.
- Keep logs on stderr or in application logs so stdout remains parseable.
- Add narrowly scoped tests that mock Playwright/LLM work rather than launching
  a real browser.

## Milestone 3: Isolated local QA runtime

- Add an explicit QA runtime mode/data-root configuration that does not touch
  production or ordinary debug data.
- Allow local/private Sentinel targets based on the QA capability, not on the
  broad Flask debug flag.
- Make automatic debug-admin login explicit/configurable so unauthenticated,
  ordinary-user, and elevated-user flows can be tested intentionally.
- Add server CLI controls needed to bind to `127.0.0.1`, disable the reloader,
  select the QA data root, and suppress scheduled/background production work.
- Ensure the target app and Sentinel report UI can use different isolated data
  roots while sharing Redis safely.
- Reuse the existing health endpoint for readiness checks.

## Milestone 4: Agent-facing report contract

- Provide a compact serializer for the CLI that does not include credentials,
  card data, full prompts, or rendered HTML.
- Preserve the detailed existing JSON report for UI/debugging.
- Record stable evidence references: report file, report URL when configured,
  screenshot paths, error/warning counts, and verdict reason.
- Capture first-party failed requests and HTTP 5xx responses as structured
  findings, while avoiding third-party console/network noise in the automated
  verdict signal.
- Add a report schema version for future-compatible clients.

## Milestone 5: Repo-scoped Codex skill

- Create `.agents/skills/sentinel-qa` using the system skill initializer.
- Keep `SKILL.md` concise and imperative. Trigger on requests to QA, smoke-test,
  exercise, or visually verify a local NabiCat feature with Sentinel.
- Teach the workflow:
  1. read the relevant subapp README and acceptance criteria;
  2. run deterministic tests first;
  3. ensure the isolated target server is ready;
  4. form a focused scenario prompt and start path;
  5. invoke the foreground Sentinel CLI;
  6. inspect compact JSON first;
  7. inspect report steps/screenshots on fail or inconclusive;
  8. perform at most one confirmation rerun when flakiness is plausible;
  9. report the run ID, verdict, reason, and evidence paths;
  10. do not represent Sentinel as a replacement for deterministic tests.
- Add only a small deterministic adapter script if it materially simplifies
  invocation; do not duplicate application logic in the skill.
- Generate `agents/openai.yaml`, validate with `quick_validate.py`, and
  forward-test the skill with a fresh subagent against a safe local scenario.

## Verification

- Run focused Sentinel unit tests after each milestone.
- Run relevant config/server tests for QA-mode behavior.
- Exercise CLI argument validation and exit-code mapping with mocked execution.
- Start no production server and touch no production data.
- If resources permit, perform one small end-to-end local run against an
  already-running or isolated target server; otherwise report that live
  Playwright/LLM execution remains unverified.
- Run `git diff --check` and review the final diff for unrelated changes and
  accidental credential/prompt exposure.

## Deferred work

- A token-authenticated asynchronous machine API or MCP server.
- CI or merge-gate enforcement.
- Automatic code changes based solely on a Sentinel failure.
- Baseline screenshot comparison, trace/video capture, and versioned scenario
  catalogs.
- Durable distributed job queues for production-scale Sentinel execution.
