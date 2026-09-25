# Task: rule-boundary attack tests (one JEV call -> deterministic edge-case tests forever)

Read `AGENTS.md`, `argus/explore/generator.py`, `argus/llm/prompts.py`, `argus/runner/author.py`,
`examples/skyops_suite.json`, `demo_app/context/PRODUCT.md`. Edit ONLY `argus/explore/boundary.py` (new),
`argus/llm/prompts.py` (add BOUNDARY_* prompts only), `argus/cli.py` (add one `boundary` command at the end),
`tests/test_boundary.py` (new). Never open .env; no git; do not start/stop servers.

- `BOUNDARY_SYSTEM/USER` prompt: given business rules (PRODUCT.md) + an existing passing test (JSON, semantic steps)
  that exercises the rule's input, return JSON `{"cases": [{"rule_ref","field_find","value","expect": "accept"|"reject",
  "why"}]}` with values exactly AT and JUST OUTSIDE each numeric/threshold rule (e.g. altitude 120 accept, 121 reject;
  battery 30% accept, 29% reject) - max 8 cases, one call.
- `async def generate_boundary_tests(settings, base_test_id) -> list[TestSpec]`: one `llm.json(tier="smart", purpose="boundary")`
  call; for each case clone the base test's authored steps, replace the value of the step whose target matches
  `field_find`, and set oracles: reject -> `text_visible` of the rule's error text if known from the base test's
  oracles else `network_absent` POST 2xx; accept -> the base test's positive oracles. Tag `boundary`, `rule_ref`.
  Baseline each via `record_baseline`; keep only those that hold on the current build; save with Memory.
- CLI: `argus boundary --test <id>`.
- Unit tests with a fake LLM + pure functions (case -> spec transformation). Run pytest for tests/test_boundary.py.
Then ONE live run against SkyOps v1.0 on http://127.0.0.1:8000 (already running) using a copy home
`.argus-b` initialised like `.argus` and authored from `examples/skyops_suite.json` (argus init/author), base test
`altitude-limit`; report the generated cases and which baselined. Keep LLM calls <= 3.
