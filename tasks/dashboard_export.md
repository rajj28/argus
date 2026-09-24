# Task: "Mission Control" dashboard + Playwright export (`argus/report/*`, `argus/export/*`)

Owner: you own ONLY `argus/report/**`, `argus/export/**`, `tests/fixtures/sample_run*.json`,
`tests/fixtures/sample_tests/*.json`, `tests/test_dashboard.py`, `tests/test_export.py`. Read `AGENTS.md`,
`docs/SPEC.md` §9, and `argus/models.py` first.

Dashboard (FastAPI + Jinja2 templates + Chart.js from `https://cdn.jsdelivr.net/npm/chart.js`):
* `create_app(home: Path) -> FastAPI` reading JSON directly from the memory folder layout in SPEC §5
  (`<home>/runs/*/report.json`, `<home>/tests/*.json`, `<home>/tests/_history/*.json`, `<home>/knowledge.json`,
  `<home>/atlas.json`, `<home>/gauntlet.json`), tolerant of missing files. Parse with the pydantic models.
* Pages: `/` (KPI tiles: tests, pass rate, healed steps, bugs found, LLM calls, $ spent, "tokens avoided vs
  LLM-per-action agent"; line chart cost-per-run + LLM calls per run; stacked bar of resolution tiers per run
  (T0 replay … T5 vision); recent runs table with verdict pills), `/runs/{run_id}` (per test: verdict pill,
  rationale, changelog citations, step timeline with tier badges + scores + screenshots thumbnails served from
  the run dir, observations, bug report markdown rendered as preformatted text, LLM call ledger),
  `/tests` and `/tests/{id}` (steps with intents + fingerprint description, version history with diffs, button
  to view Playwright export), `/memory` (attribute stability bar chart, remembered decisions, atlas as a simple
  graph with vis-network from jsdelivr), `/gauntlet` (benchmark table + charts if `gauntlet.json` exists).
  JSON API mirrors: `/api/runs`, `/api/runs/{id}`, `/api/tests`.
* Look: dark mission-control theme (near-black background, cyan/amber/green/red accents, monospace numbers),
  responsive, no build step, auto-refresh the home page every 5 s. Verdict colours: PASS green, COSMETIC_DRIFT
  teal, INTENDED_CHANGE blue, FEATURE_REMOVED grey, BUG red, NEEDS_REVIEW amber, INFRA purple.
* Screenshot route: `/runs/{run_id}/shots/{name}` (path-traversal safe).

Export: `to_playwright_ts(test: TestSpec, base_url: str) -> str` producing a valid Playwright Test file:
prefer `getByTestId` (if testid attr) → `getByRole(role, {name})` → `getByLabel(label)` → `getByPlaceholder` →
`getByText` → `locator(css)`; `fill/click/selectOption/check/press`; `expect(page).toHaveURL(...)` for
url_pattern expectations; oracles `text_visible` → `expect(page.getByText(...)).toBeVisible()`. Comment each step
with its intent. Also `export_all(home, out_dir)`.

Tests: create realistic fixtures (a RunReport with 3 tests: one PASS with T0 steps, one COSMETIC_DRIFT with T1
heals, one BUG with network 500 evidence; plus 2 TestSpecs) and check every page returns 200 with httpx
`TestClient`, and the export contains the expected locator calls.
