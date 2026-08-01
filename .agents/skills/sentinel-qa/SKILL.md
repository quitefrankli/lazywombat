---
name: sentinel-qa
description: Run Sentinel exploratory browser QA against the local NabiCat debug server and interpret its persisted evidence. Use when asked to QA, smoke-test, exercise, or visually verify a locally implemented NabiCat feature after deterministic tests pass; do not use as a replacement for unit or integration tests.
---

# Sentinel QA

Exercise one focused user journey through Sentinel and return its evidence-backed
result to the user.

## Workflow

1. Read `AGENTS.md`, `web_app/sentinel/README.md`, and the README for the
   subapp under test. Read `UIUX.md` when the change affects UI or UX.
2. Identify the acceptance criteria, start path, required account role, and
   one primary user journey. Ask for clarification only when those cannot be
   inferred safely.
3. Run the smallest relevant deterministic tests first. Stop and report their
   failures before invoking Sentinel.
4. Confirm the usual debug server is listening on `127.0.0.1:12345` and that
   `/api/health` succeeds. Reuse it when available. If it is absent and this is
   not a production host, start it with the ordinary launcher:

   ```bash
   python -m web_app --debug --port 12345
   ```

   CLI reports and screenshots use the normal `~/.nabicat/data/sentinel`
   store, so an already-running ordinary Sentinel UI can observe them. Do not
   start another app process against normal data solely to display a report.
5. Write a concrete prompt that names the visible behaviour to exercise and
   the observable evidence for success. Avoid asking for broad exploration
   when the change has focused acceptance criteria.
6. Invoke the foreground runner with the loopback target URL, focused prompt,
   appropriate device and demographic, and JSON output. Do not work around
   runner safety restrictions. For example:

   ```bash
   python -m web_app.sentinel run \
     --target http://127.0.0.1:12345/feature \
     --prompt "Verify the visible acceptance criterion end to end." \
     --json
   ```

   Pass `--report-url-base` only when an existing ordinary app server exposes
   the normal Sentinel store.
7. Treat the process result as follows:
   - exit `0`: pass;
   - exit `1`: the exercised product behaviour failed;
   - exit `2`: inconclusive or execution/infrastructure failure;
   - exit `130`: cancelled or interrupted.
8. Read the compact JSON first. On fail or inconclusive, inspect the persisted
   report steps and only the screenshots needed to understand the evidence.
9. If transient timing or agent navigation plausibly caused the result,
   perform at most one confirmation rerun with the same scenario. Do not loop
   until a run passes.
10. Report the deterministic test result, Sentinel run ID, verdict, reason,
    report path or URL, inspected evidence, and whether a rerun agreed.

## Guardrails

- Keep Sentinel advisory unless the repository explicitly promotes a stable
  scenario to a gate.
- Do not claim unvisited behaviour was verified.
- Do not infer a pass from a completed process, a generated report, or the
  absence of severe findings; require the explicit `pass` verdict.
- Do not include credentials, tokens, card data, full prompts, cookies, or
  other secrets in the summary or application logs.
- Do not make code changes solely from one potentially flaky exploratory
  result. Establish reproducibility or corroborate it with deterministic
  evidence first.
- Preserve every run's artifact paths so another agent or human can inspect
  the same evidence.
