# Task: fix four robustness bugs found by the real-world runs (unit tests only - NO servers, NO browsers
# beyond what pytest fixtures already use; the machine is low on memory)

Read `AGENTS.md`. You may edit ONLY the files named per bug and `tests/test_robustness.py` (new).
Never open .env; no git; do not start servers or Docker.

1. `argus/runner/author.py`: `author()` assumes every step has `"find"` (KeyError on `{"action": "wait", "value": "1500"}`
   and on `goto`/`press` without target). Support: `goto` (value = path), `wait` (value = ms), `press` without
   `find` (page keyboard). `spec_from_dict` already maps them; only the first authoring pass needs it.
2. `argus/runner/runner.py` `_act`: `select` / `fill` / `click` fail permanently when the element handle
   detaches (element re-rendered between snapshot and action, seen on SauceDemo performance_glitch_user).
   In `_execute`, on an error message containing "detached" / "not attached" / "Element is not attached",
   take a fresh snapshot, re-resolve the same step with `resolve(...)` (no LLM), and retry the action ONCE.
3. `argus/llm/client.py` `LLMClient.__init__`: constructing `AsyncOpenAI(api_key=settings.api_key)` raises
   "Missing credentials" when the primary OpenRouter key is empty although other providers (openrouter2,
   groq...) are configured. Build the default OpenRouter client only if a key exists (else leave it None and let
   `_client_for("openrouter")` return None so the chain skips it).
4. `argus/runner/runner.py` `run_test`: exceptions from `page.goto` inside the step loop (e.g.
   `net::ERR_NAME_NOT_RESOLVED`) and any other unexpected exception must NOT crash `run_suite`; the test gets
   verdict `INFRA` (status "error", rationale with the error) and the suite continues with the next test.
   Also make `run_suite` wrap each `run_test` in try/except with the same INFRA fallback.
Write focused unit tests in `tests/test_robustness.py` (mock pages/clients with simple fakes; no network),
then run `.venv/Scripts/python.exe -m pytest -q tests/ --ignore=tests/test_demo_app.py` and make everything
pass. Report changes per bug.
