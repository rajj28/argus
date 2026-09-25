# Task: "jury of models" for ambiguous triage (JEV's multi-model advantage)

Read `AGENTS.md`, `argus/triage/triage.py` (`_llm_judge`), `argus/llm/client.py`, `argus/llm/providers.py`.
Edit ONLY `argus/triage/triage.py`, `argus/llm/client.py` (add a method), `argus/config.py` (one setting),
`tests/test_jury.py` (new). Never open .env; no git; no servers.

Design:
- `LLMClient.json_from(model_spec, *, purpose, system, user, max_tokens)` -> dict: call ONE specific model spec
  (e.g. "nvidia/nemotron-3-super-120b-a12b:free", "openrouter2:qwen/qwen3.8-27b:free", "groq:openai/gpt-oss-120b")
  with the same cache / ledger / budget / circuit-breaker behaviour as `json()`.
- Settings: `jury: list[str]` default = 3 DIVERSE models from different families, preferring JEV (openrouter) models:
  nemotron-3-super (openrouter), qwen3.8-27b (openrouter), gemma-4-31b-it (openrouter); each juror falls back to the
  same family on openrouter2 / groq if its provider is exhausted. `jury_enabled: bool = True`.
- In `_llm_judge`: if jury enabled, ask the jurors concurrently (asyncio.gather, same TRIAGE prompt). Each vote passes
  the existing citation guard individually. Aggregate: unanimous -> that category, confidence = mean;
  2/3 majority -> that category, confidence = mean * 0.85, rationale notes the dissent; no majority or < 2 valid
  votes -> NEEDS_REVIEW with rationale "jury split: ...". Record votes in `Verdict.evidence_refs` as
  "juror:<model>=<CATEGORY>(<conf>)" and set decided_by="llm". Fall back to the single judge if < 2 jurors reachable.
- Unit tests with a fake LLM: unanimous, majority, split, one juror down, citation guard applied per vote.
Run `.venv/Scripts/python.exe -m pytest -q tests/test_jury.py tests/test_llm_client.py` until green. Report briefly.
