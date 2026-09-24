# Task: autonomous exploration + test generation (`argus/explore/*`)

Owner: you own ONLY `argus/explore/explorer.py`, `argus/explore/generator.py`, `tests/test_explore.py`.
Read `AGENTS.md`, `docs/SPEC.md` (§2, §5, §6, §8), `argus/models.py`, `argus/browser/snapshot.py`,
`argus/browser/effects.py`, `argus/runner/runner.py` (use `RunContext`, `ensure_auth`, `record_baseline`,
`_act` semantics), `argus/llm/prompts.py` (`GENERATE_SYSTEM/USER`), `argus/llm/client.py`, `argus/memory/store.py`.

## explorer.py — deterministic, zero LLM calls
`async def explore(settings, max_states: int = 25, max_actions: int = 90, log=print) -> dict`
* Launch chromium (settings.headless), login once via `ensure_auth` (credentials in settings) and use that
  storage state for every context. Start at `settings.base_url + "/"`.
* A **state** = page condition identified by `structural_signature(snapshot)`. Each state records:
  `{"id": "S3", "url": normalize_path(url), "entry_url": full url, "path": [steps from entry_url to reach it],
    "title", "headings", "alerts", "elements": [ {"id": "S3.e7", "fp": Fingerprint json, "desc": one-line
    description like compact_for_llm, "enabled": bool} ...interactive elements only, max 60 ],
    "forms": [...optional...]}`.
* BFS. To expand a state: open a fresh page at `entry_url`, replay its `path` (steps resolved with
  `argus.healing.resolver.resolve` + the same action helper as the runner), then try candidate actions:
  - same-origin links not yet visited (skip `/__admin`, `logout`, external, `mailto:`, downloads);
  - enabled buttons whose name does NOT match the deny regex
    `delete|remove|log ?out|sign ?out|abort|cancel|pay|purchase|export|download` ;
  - before clicking a "progress" button (name matches `next|continue|save|submit|launch|create|start|apply|
    confirm|sign in|log in|search` or type=submit), FIRST fill the visible, empty, enabled form controls in
    the same `context` with heuristic valid data: email → `qa.bot@example.com`; password → credentials;
    number → midpoint of [min,max] (default 5); tel → `+919876543210`; url → `https://example.com`; date →
    tomorrow ISO; text whose name/label contains "name" → `Argus ${unique}`; other text → `Argus test`;
    textarea → `Automated exploration note`; select → first option with a non-empty value that is not a
    placeholder; radio groups → first ENABLED option; leave checkboxes as they are; skip search boxes.
    These fills become part of the child state's `path` when the click changes the state.
  - after each action capture effects (`EffectRecorder`): URL change → new state with `entry_url` = new url and
    empty path; same URL but different signature → new state with `entry_url` = parent entry_url and
    `path` = parent path + fills + click; unchanged → ignore.
  - record every transition `{"from","to","action","target": fp json,"intent": "click 'New mission'",
    "network": [mutating calls as "POST /api/missions 201"]}`.
* Stop at `max_states` / `max_actions`. Save with `Memory.save_atlas(atlas)` and return it. Log progress lines.
* Must fully explore the SkyOps demo (`demo_app`, run it on port 8000): dashboard, drone detail, missions, all
  4 wizard panels (so the atlas contains the "Select drone" and "Flight parameters" panels with a DISABLED
  low-battery drone), mission detail, logs, settings.

## generator.py — 1 LLM call to propose, 0 to compile
* `def atlas_digest(atlas, max_chars=24000) -> str`: compact text: per state `S3 /missions/new "Plan a
  mission" path: [..]` + its element lines `S3.e7 textbox "Mission name" {required}` + transitions
  `S2 --click "New mission"--> S3 [POST ...]`. Include disabled elements and alerts (they reveal rules).
* `async def propose(settings, atlas, llm) -> list[dict]`: one `smart` call with `GENERATE_SYSTEM/USER`
  (product context = `settings.product_context()`), returns raw test dicts.
* `def to_spec(raw, atlas, idx) -> TestSpec | None`: map `"S3.e7"` → Fingerprint from the atlas; build
  `Step`s (ids `s1..`), prepend the target state's `path` steps when the first referenced element lives in a
  state that is not the `start_url` page, add `tags`, `requires_login`, oracles (`Assertion`); test id =
  slug of the name (unique). Tests whose name/tags say negative get the `negative` tag.
* `async def generate(settings, atlas=None, max_tests=8, log=print) -> list[TestSpec]`: load atlas (explore if
  missing), propose, convert, then **compile**: for each spec call `record_baseline` (with a `RunContext`
  built from `Memory(settings.home)`, the LLM client, a run dir from `memory.new_run_dir()`), keep specs that
  return non-None, save them with `Memory.save_test(spec, TestChange(version=1, kind="created", summary=...))`
  and return them. Also always add a deterministic `login` test (tag `auth`, requires_login False) built from
  the login page fingerprints if a login form exists (fill user, fill password, click submit; oracle:
  `url_matches` the post-login path).
* When the LLM is unavailable, fall back to deterministic journeys from the atlas: one "navigate to X" smoke
  test per top-level page (oracle `text_visible` of its main heading) + the wizard path found by exploration.
  The product must still produce a useful suite with zero model calls.

## Tests (no network, no LLM)
Unit-test `atlas_digest`, `to_spec` (path prepending, fingerprint mapping, negative tag) and the heuristic
value filler with small in-memory atlases. Then run a real exploration against `demo_app` (start it with
`.venv/Scripts/python.exe -m demo_app.server --port 8000` in the background, credentials
`pilot@skyops.io` / `flysafe123`) and print the number of states found and their headings.
