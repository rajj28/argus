# Argus — Technical Spec (contract for all coding agents)

> Argus is an autonomous UI-testing agent: *the LLM compiles, the runtime replays.*
> Models are called only on novelty (unknown page, broken locator, unexplained change).
> Data contracts live in `argus/models.py` — import from there, never redefine them.

## 0. Ground rules for every contributor (human or agent)

* Python 3.11, async Playwright (`playwright.async_api`), pydantic v2, FastAPI, typer, rapidfuzz.
* Venv: `.venv` (run with `.venv/Scripts/python.exe` on Windows). Do **not** add heavy deps (no LangChain, no torch).
* **Never** read, print, log or commit `.env` / API keys. Get the key only via `argus.config.load_settings()`.
* Only touch files your task owns. Shared contracts (`argus/models.py`, `docs/SPEC.md`) are read-only for you;
  if you need a change, write it in your final summary instead.
* Every module gets a small pytest file in `tests/` that runs **without network and without an LLM**
  (use local HTML fixtures served by `page.set_content` or a `file://` URL).
* Windows paths: use `pathlib.Path`, open text files with `encoding="utf-8"`.
* Keep functions small, typed, with short docstrings. No placeholder `TODO` code paths in the happy path.

## 1. Repository layout

```
argus/
  models.py            # contracts (owned by architect)
  config.py            # Settings, load_settings(), model tiers, thresholds
  browser/
    snapshot.js        # DOM distiller (injected)            -> PageSnapshot
    snapshot.py        # take_snapshot(page) / element_handle(page, ref) / compact_for_llm(snap)
    effects.py         # EffectRecorder: before/after, network, console, page errors, settle
  healing/
    similarity.py      # Similo-style multi-attribute scoring, synonyms, learned weights
    resolver.py        # tiered resolution cascade (T0 replay, T1 similarity, T3 LLM, T5 vision)
  llm/
    client.py          # OpenRouter client: tiers, fallback chain, disk cache, cost ledger, budget
    prompts.py         # all prompt templates (heal, replan, triage, generate)
  memory/
    store.py           # JSON-backed memory (.argus/): tests+versions, atlas, knowledge, runs
  runner/
    runner.py          # run_test / run_suite: execute, verify effects, out-of-order, replan, update
    assertions.py      # deterministic assertion checks
  triage/
    triage.py          # verdict engine: rules -> (LLM judge w/ changelog, citation-verified)
  explore/
    explorer.py        # deterministic BFS crawler -> atlas (no LLM)
    generator.py       # atlas + product context -> TestSpecs (1-3 LLM calls), compile run
  chaos/
    gauntlet.py        # benchmark: N mutation trials + bug-injection trials -> gauntlet.json
  report/
    dashboard.py       # FastAPI "Mission Control" dashboard
    templates/, static/
  export/
    playwright_export.py  # TestSpec -> Playwright Test (.spec.ts)
  mcp_server.py        # MCP tools for coding agents
  cli.py               # typer CLI: init, explore, generate, run, gauntlet, dashboard, export, mcp
demo_app/              # "SkyOps" drone-ops console, versions 1.0-1.3 + chaos + bug flags
tests/                 # pytest
.argus/                # memory (runtime; runs/ and llm_cache/ are gitignored)
```

## 2. Perception — `argus/browser/`

### 2.1 `snapshot.js`
A single JS arrow function evaluated with `page.evaluate(JS, opts)`; returns a plain object shaped like
`PageSnapshot` (without `signature`, computed in Python). `opts = {maxElements: 350}`.

* Collect **interactive** elements (also inside open shadow roots): `a[href]`, `button`, `input:not([type=hidden])`,
  `select`, `textarea`, `summary`, `[contenteditable=true]`, `[onclick]`, `[tabindex]:not([tabindex="-1"])`, and roles
  `button link tab menuitem checkbox radio switch option combobox textbox searchbox slider spinbutton`.
  Also `label` elements are NOT collected as targets (they are used for names).
* Collect **anchors** with `interactive=false`: visible `h1..h4`, `[role=alert]`, `[role=status]`, `[aria-live]`,
  `dialog[open]`/`[role=dialog]` titles, and elements whose class contains `error|invalid|toast|alert` with text.
* Skip invisible elements (use `el.checkVisibility({checkOpacity:true, checkVisibilityCSS:true})` when available,
  else computed style + non-zero rect). Keep disabled elements (with `enabled=false`).
* Per element compute: `tag` (lowercase), `role` (explicit, else implicit map: a[href]→link, button→button,
  input[type=checkbox|radio]→checkbox|radio, input[type=submit|button|reset]→button, input number→spinbutton,
  input range→slider, input search→searchbox, other inputs & textarea→textbox, select→combobox, h1-6→heading,
  summary→button), `name` (aria-labelledby → aria-label → associated label (`label[for]` or wrapping label, excluding
  the control's own value) → placeholder → title → innerText → value (buttons) → alt of child img; collapse
  whitespace, max 80 chars), `text` (innerText max 80), `attrs` (whitelist only: `id name type class href placeholder
  aria-label title alt value data-testid data-test data-test-id data-cy data-qa for required min max maxlength
  pattern disabled readonly aria-expanded aria-selected aria-checked`; never include values of password inputs; for
  text inputs include `value` only if non-empty, max 40 chars), `label`, `context`, `neighbor_text`, `xpath`
  (absolute, with 1-based indices), `css` (id-anchored if an ancestor has a unique id, else nth-of-type chain),
  `bbox` (page coords = rect + scroll, rounded ints), `visible`, `enabled` (not `disabled`, not
  `aria-disabled=true`, not inside disabled fieldset), `editable`, `checked`, `interactive`, `in_dialog`.
* `context` = first non-empty of: dialog title (if inside dialog) · table-row text (inside `tr`: row innerText minus
  own text, max 60) · nearest card/section/li/form ancestor's first heading (h1-h6/legend/[role=heading]) text ·
  nearest landmark (`nav/header/main/aside/footer/form/section[aria-label]`) accessible name or tag.
* `neighbor_text` = texts of up to 3 closest text-bearing siblings/parent siblings (not own), joined by " | ", max 80.
* Store element references in `window.__argus = {refs: [...]}`; element `ref` = `"e" + index`.
* Page-level: `url`, `title`, `headings` (visible h1..h3, ≤20), `alerts` (≤10 texts, ≤120 chars each),
  `text_digest` (visible body text collapsed, ≤1500 chars), `viewport {w,h}`.
* Must run in < 150 ms on a 1,000-node page. No external libraries. Never mutate the app DOM (no attributes added).

### 2.2 `snapshot.py`
```python
async def take_snapshot(page, max_elements: int = 350) -> PageSnapshot     # computes .signature
def structural_signature(snap: PageSnapshot) -> str   # sha1 of sorted (role, normalized name) of interactive elems
                                                      # + normalized url path; ignores counts of repeated rows
async def element_handle(page, ref: str) -> ElementHandle | None             # window.__argus.refs[i]
def compact_for_llm(snap: PageSnapshot, limit: int = 120, only_interactive=False) -> str
    # one line per element:  [e12] button "Add drone" (in: Fleet) {id=add, disabled}
    # plus headings + alerts header. Target: ~15 tokens per element.
def normalize_path(url: str) -> str   # strip origin, numeric/uuid/hex(>=8) segments -> ":id", drop query+hash
```

### 2.3 `effects.py`
```python
class EffectRecorder:
    def __init__(self, page): ...     # attaches page.on(request/response/requestfailed/console/pageerror/dialog)
                                       # dialogs are auto-accepted and their message recorded
    async def begin(self) -> None      # mark positions, remember url/title/headings/alerts/signature
    async def end(self, settle_ms: int = 4000) -> Effects   # waits for settle, then diffs
    def all_page_errors(self) -> list[str];  def all_console_errors(self) -> list[str]
async def wait_for_settle(page, recorder, timeout_ms=4000)
    # done when: no in-flight fetch/xhr for 300 ms AND no DOM mutation for 250 ms (MutationObserver) or timeout.
```
Only record `fetch`, `xhr` and `document` requests to the app's own origin. Paths normalized with `normalize_path`.

## 3. Healing — `argus/healing/`

### 3.1 `similarity.py` (pure, no I/O)
* `DEFAULT_WEIGHTS = {testid:3.0, name:2.5, id:1.5, name_attr:1.5, role:1.5, label:1.5, context:1.5, text:1.5,
  tag:1.0, type:1.0, placeholder:1.0, href:1.0, neighbor_text:1.0, class:0.5, xpath:0.5, position:0.5, size:0.3,
  css:0.3, title:0.5, alt:0.5}` (`testid` = first of data-testid/data-test/data-test-id/data-cy/data-qa,
  `name_attr` = the HTML `name` attribute).
* `score(target: Fingerprint, cand: ElementInfo, weights) -> tuple[float, dict[str,float]]`:
  weighted mean over attributes **present in target** (missing in candidate ⇒ similarity 0). Result ∈ [0,1].
  - exact attrs (id, name_attr, testid, tag, role, type): 1/0 (role: treat `button`≈`link` as 0.5).
  - text attrs (name, text, label, placeholder, context, neighbor_text, title, alt): `text_sim` =
    max(rapidfuzz `token_set_ratio`/100, synonym score). Synonym groups (partial credit 0.85):
    {log in, login, sign in, signin} {log out, logout, sign out} {sign up, register, create account}
    {next, continue, proceed} {back, previous} {save, save changes, update, apply} {submit, send, confirm}
    {delete, remove} {new, create, add} {cancel, close, dismiss} {search, find, filter} {launch, start, go, run}
    {settings, preferences} {edit, modify}. Also strip trailing ids/numbers when comparing.
  - class: Jaccard of class tokens after dropping hash-like tokens (`/[0-9]/ && len>=5`, `^css-`, `^sc-`,
    `__[a-z0-9]{4,}$` suffixes are stripped: `Button_primary__x7f2a` → `button`,`primary`).
  - xpath / css: rapidfuzz Levenshtein normalized similarity over path segments.
  - href: text_sim over normalized paths.
  - position: `max(0, 1 - dist(centers)/600)`; size: `min(area)/max(area)`.
* `compatible(action, cand) -> bool`: fill→editable; select→`select`/combobox/listbox; check/uncheck→checkbox/
  radio/switch; click/hover→visible & enabled & (interactive); goto/press/wait→n/a.
* `rank(target, snap_elements, action, weights, top_k=5) -> list[Candidate]` where
  `Candidate = (element: ElementInfo, score: float, breakdown: dict)` sorted desc (only compatible, interactive).
* `effective_weights(base, stability: dict[str,float]) -> dict` = `base[a] * (0.35 + 0.65*stability.get(a,1.0))`.
* `changed_attributes(old: Fingerprint, new: Fingerprint) -> dict[str,bool]` (True = unchanged) used to learn.

### 3.2 `resolver.py`
```python
class Resolution(BaseModel):
    element: ElementInfo | None; tier: int | None; score: float = 0; margin: float = 0
    method: Literal["replay","similarity","llm","vision","not_found"]
    candidates: list[dict]   # top-5: {ref, desc, score, breakdown}
async def resolve(snap: PageSnapshot, step: Step, weights, llm: LLMClient|None, cfg, *, allow_llm=True) -> Resolution
```
Cascade (thresholds from `cfg.thresholds`):
* **T0 replay** — top candidate has `score ≥ replay (0.90)`, `margin ≥ 0.10`, and same xpath or css as the target.
* **T1 similarity heal** — `score ≥ accept (0.72)` and `margin ≥ 0.12` (or `score ≥ 0.92` and margin ≥ 0.05).
* **T3 LLM heal** — if `score ≥ llm_min (0.35)`: `fast` tier picks among top-5 (prompt `HEAL`); accept if returned
  ref ∈ top-5 and confidence ≥ 0.6.
* otherwise `not_found` (the runner then tries T2 out-of-order and T4 replan).
`resolve` never clicks anything. It is pure w.r.t. the page (except the LLM call).

## 4. LLM — `argus/llm/`

### 4.1 `client.py`
```python
class LLMClient:
    def __init__(self, settings: Settings)
    async def json(self, *, tier: Literal["fast","smart","vision"], purpose: str, system: str, user: str,
                   images: list[bytes] | None = None, max_tokens: int = 700) -> dict
    ledger: list[LLMCallRecord]            # appended per call (including cache hits with cached=True)
    def take_ledger(self) -> list[LLMCallRecord]   # return & clear (runner calls it per test)
class LLMUnavailable(Exception); class BudgetExceeded(Exception)
```
* OpenAI SDK (`openai.AsyncOpenAI(base_url=settings.llm_base_url, api_key=settings.api_key)`), OpenRouter headers
  `HTTP-Referer: https://github.com/argus-qa`, `X-Title: Argus`.
* Model **fallback chain** per tier from `settings.models[tier]`; on 429/5xx/timeout/empty/invalid JSON try next model;
  exponential backoff (1s, 2s, 4s) on 429 while respecting 20 req/min.
* Ask for JSON: pass `response_format={"type":"json_object"}` (retry without it if the provider rejects), and
  extract the first balanced `{...}` if the model wraps it in prose/markdown. Strip `<think>...</think>`.
* Send `extra_body={"reasoning": {"effort": "low", "exclude": True}}` for fast tier.
* **Disk cache** `.argus/llm_cache/<sha256>.json` keyed by (tier, system, user, image hashes); hits cost 0, are
  recorded with `cached=True`. Disabled when `settings.llm_cache=False`.
* **Cost ledger**: tokens from `usage`; `cost_usd` = 0 for `:free` models else settings price table;
  `list_cost_usd` from `settings.reference_prices[tier]` ($/M in, $/M out).
* **Budget**: `settings.max_llm_calls_per_run` (non-cached calls) → raise `BudgetExceeded`.
* No key / `ARGUS_LLM=off` → raise `LLMUnavailable` (callers must degrade gracefully, never crash).

### 4.2 `prompts.py`
Plain string templates + tiny builder functions. Prompts must demand **JSON only** and give the exact schema.
`HEAL`, `REPLAN`, `TRIAGE`, `GENERATE`, `LLM_CHECK`. (Architect owns wording; implement builders.)

## 5. Memory — `argus/memory/store.py` (JSON files under `settings.home`, default `.argus/`)
```
.argus/config.json            Settings persisted by `argus init`
.argus/tests/<id>.json        current TestSpec
.argus/tests/_history/<id>.v<N>.json   archived versions
.argus/atlas.json             {states:{sid:{url_pattern,title,headings,signature,summary,first_seen,last_seen,visits}},
                               transitions:[{from,to,action,target:Fingerprint,intent}]}
.argus/knowledge.json         {stability:{attr:{same:int,changed:int}}, decisions:{sig:Verdict}, allow_console:[...],
                               auth:{storage_state_path}}
.argus/runs/<run_id>/report.json + screenshots/*.png
```
```python
class Memory:
    def __init__(self, home: Path)
    def list_tests(self, status: str | None = "active") -> list[TestSpec]
    def load_test(self, test_id) -> TestSpec
    def save_test(self, test: TestSpec, change: TestChange | None = None) -> TestSpec   # archives old version,
                                                                       # bumps version when change given
    def stability(self) -> dict[str, float]      # Beta(1,1) posterior mean of "unchanged" per attribute
    def learn_from_heal(self, old: Fingerprint, new: Fingerprint) -> dict[str,bool]
    def get_decision(self, sig: str) -> Verdict | None ; def put_decision(self, sig: str, v: Verdict)
    def atlas(self) -> dict ; def save_atlas(self, atlas: dict)
    def new_run_dir(self) -> tuple[str, Path] ; def save_run(self, report: RunReport) ; def list_runs(self) -> list[RunReport]
```
Writes are atomic (write tmp + `os.replace`). **Verified-only memory:** the runner commits heals/decisions only
after the test verdict is PASS / COSMETIC_DRIFT / INTENDED_CHANGE (never from a BUG run).

## 6. Runner — `argus/runner/runner.py`
```python
class RunContext: settings, memory, llm, browser, product_context: str, changelog: str, vars: dict, run_dir
async def run_suite(settings, *, test_ids=None, label="", update=True) -> RunReport
async def run_test(test: TestSpec, ctx: RunContext) -> TestResult
async def record_baseline(test: TestSpec, ctx) -> TestSpec   # executes & fills step.expect from observed effects
```
Loop per test (fresh browser context; `storage_state` for `requires_login` tests):
1. `goto(start_url)`; `pending = steps`.
2. snapshot → `resolve(step)`.
   * found → execute (`click/fill/select/check/press/hover`) with EffectRecorder → compare with `step.expect`
     (network method+path+status-class, url pattern, headings) → observations. Tier>0 ⇒ `locator_healed`.
   * not found → **T2 out-of-order**: try the next 3 pending steps with strict resolution (score ≥ 0.85, no LLM);
     if one matches, execute it now (`step_reordered`) and keep the current one pending.
   * still not found → **T4 replan** (`smart`→`fast`, prompt `REPLAN`, compact snapshot + goal + remaining intents +
     alerts/validation messages): `act` (≤5 actions on this page → `step_added`), `skip_step` (`step_missing`),
     `blocked` (`goal_unreachable`, stop). Max 4 replans per test.
3. After all steps: evaluate `oracles` → `assertion_failed`; collect page errors, console errors (minus allowlist),
   5xx → observations.
4. `verdict = triage(...)`. Then: COSMETIC_DRIFT → save healed fingerprints (TestChange `healed`), learn stability;
   INTENDED_CHANGE → save new step list (TestChange `updated` with diff); FEATURE_REMOVED → status `retired`;
   BUG → keep test unchanged and write `bug_report` markdown (repro steps, expected vs actual, evidence).
5. Screenshot per step (jpeg, quality 60, `screenshots/<test>_<step>.jpg`); full-page on failure.
`naive_tokens_estimate` += len(compact_for_llm(snapshot))/4 + 350 per executed step.

## 7. Triage — `argus/triage/triage.py`
```python
async def triage(test, result: TestResult, ctx) -> Verdict
def deviation_signature(test_id, observations) -> str
```
Rules first (no LLM):
* no observations → `PASS`.
* only `locator_healed` (any tier) and oracles pass → `COSMETIC_DRIFT` (action `test_healed`).
* hard signals (`page_error`, `network_error` 5xx, new `console_error`, `assertion_failed` on an oracle with
  `rule_ref`) and **no** structural change → `BUG` (conf 0.9, action `bug_reported`).
* remembered decision for `deviation_signature` → reuse (`decided_by="memory"`).
* everything else (structural changes, weak mismatches, goal_unreachable, assertion failures that the changelog
  might explain) → **LLM judge** (`smart`, prompt `TRIAGE`) with test goal, step outcomes, observations, product
  rules and changelog (Testora-style NL-intent oracle).
**Asymmetric-risk guard:** `INTENDED_CHANGE`/`FEATURE_REMOVED` require confidence ≥ 0.7 **and** ≥1
`changelog_refs` quote that literally occurs in the changelog (normalized substring match) — otherwise downgrade
to `NEEDS_REVIEW`. LLM unavailable ⇒ `NEEDS_REVIEW` (or `BUG` if hard signals).

## 8. Explore & generate — `argus/explore/`
* `explorer.py`: `async def explore(settings, max_states=25, max_actions=80) -> dict` — deterministic BFS over states
  (state id = structural signature). Logs in with `settings.credentials` if a password field is found. Clicks
  same-origin links and non-destructive buttons (deny regex: `delete|remove|log ?out|sign ?out|abort|cancel|pay|
  purchase`), fills forms with heuristic data (email/password/number within min-max/date/select first real
  option/checkbox) and submits once. Skips `/__admin`. Records states & transitions into the atlas; no LLM.
* `generator.py`: `async def generate(settings, atlas, product_context) -> list[TestSpec]` — 1 `smart` call
  (`GENERATE`) proposes 5-8 journeys + business-rule negative tests with oracles, referencing atlas elements;
  then `compile` each journey in the browser (resolution against atlas fingerprints, LLM only on miss),
  `record_baseline`, drop oracles that fail on the baseline (they are wrong), save as `origin="generated"`.
  Also `async def generate_from_goal(settings, goal: str) -> TestSpec` (agentic, page-level batching via `REPLAN`).

## 9. Report, export, MCP, CLI
* Dashboard (FastAPI + Jinja2 + Chart.js from jsdelivr): `/` KPIs + cost-per-run + tier mix per run + recent runs;
  `/runs/{id}`; `/tests` + `/tests/{id}` (history & diffs, Playwright export); `/memory` (stability bars, decisions,
  atlas graph); `/gauntlet`. Dark "mission control" theme. Reads only through `Memory`.
* `playwright_export.py`: `def to_playwright_ts(test: TestSpec, base_url) -> str` using getByRole/getByLabel/
  getByTestId/getByText in the best available order, falling back to css.
* `mcp_server.py` (FastMCP): tools `explore_app`, `generate_tests`, `run_tests`, `verify_change(description)`,
  `get_last_report`.
* `cli.py` (typer): `init --url --context --user --password`, `explore`, `generate [--goal]`, `run [--label]
  [--no-update] [--headed]`, `gauntlet [--trials] [--bugs]`, `dashboard [--port]`, `export`, `mcp`.

## 10. Demo target — `demo_app/` (SkyOps) — see `tasks/demo_app.md`.
