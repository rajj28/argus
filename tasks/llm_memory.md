# Task: LLM client + memory store (`argus/llm/client.py`, `argus/memory/store.py`)

Owner: you own ONLY `argus/llm/__init__.py`, `argus/llm/client.py`, `argus/memory/__init__.py`,
`argus/memory/store.py`, `tests/test_llm_client.py`, `tests/test_memory.py`. Read `AGENTS.md`,
`docs/SPEC.md` §4.1 and §5, `argus/models.py` and `argus/config.py` first. Implement exactly those interfaces.

LLM client requirements (SPEC §4.1):
* `LLMClient(settings)` with `async json(...)` returning a parsed dict; model fallback chain per tier; retries with
  backoff on 429/5xx/timeouts (timeout 60 s per call); JSON extraction robust to markdown fences, prose, and
  `<think>` blocks; `response_format={"type":"json_object"}` with a retry without it on 400; images sent as
  `data:image/jpeg;base64,...` content parts for the vision tier.
* Disk cache keyed by sha256 of (tier, system, user, image hashes) at `settings.home/llm_cache/`.
* `ledger` of `LLMCallRecord` (models.py) with tokens, cost (0 for ":free" models), list cost from
  `settings.reference_prices`, latency, cached flag. `take_ledger()` returns and clears it.
* Budget: count non-cached successful calls per client instance; raise `BudgetExceeded` beyond
  `settings.max_llm_calls_per_run`. Raise `LLMUnavailable` when `settings.llm_enabled` is False or all models fail.
* A module-level simple rate limiter: at most 18 requests / 60 s across the process.
* Tests: monkeypatch the OpenAI client with a fake (no network) to cover fallback on 429, JSON extraction from
  messy text, cache hit (no second API call), ledger accounting, budget exceeded, disabled -> LLMUnavailable.

Memory requirements (SPEC §5):
* JSON files under `settings.home` with atomic writes (tmp + `os.replace`), UTF-8, pretty-printed.
* Test versioning: `save_test(test, change)` archives the previous file to `tests/_history/<id>.v<N>.json`,
  bumps `version`, appends `change` to `history`. Without `change` it just saves.
* `stability()` = Beta(1,1) posterior mean per attribute of "unchanged" ((same+1)/(same+changed+2)), default 1.0
  for unseen attributes. `learn_from_heal(old, new)` uses `argus.healing.similarity.changed_attributes` if it
  exists, else compare fields directly (id, name_attr, testid, class, text, name, label, xpath, css, role, tag,
  context, placeholder, href).
* decisions store (signature → Verdict), atlas load/save, runs: `new_run_dir()` creates
  `runs/<YYYYmmdd-HHMMSS>-<4hex>/screenshots`, `save_run`, `list_runs()` sorted by time.
* Tests with `tmp_path`: round-trips, versioning/archiving, stability math, run listing.
