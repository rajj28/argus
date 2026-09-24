# Task: Groq gpt-oss returns empty content -> fix the LLM client
Read AGENTS.md. Edit ONLY argus/llm/client.py, argus/llm/providers.py, tests/test_llm_client.py. Never open .env; no git.
Symptom: via the mesh, `groq:openai/gpt-oss-20b` and sometimes `groq:openai/gpt-oss-120b` return empty message content
("model returned non-JSON output: ''"). gpt-oss models are reasoning models: with response_format=json_object and a small
max_tokens the hidden reasoning consumes the budget and content is empty.
Fix in `_try_one_model` for provider == "groq" (and any model id containing "gpt-oss"): send `reasoning_effort="low"`
(Groq OpenAI-compatible param; pass via extra_body if the SDK rejects it), raise max_tokens to at least 1024 for these models,
and if content is empty but the response has a `reasoning`/`reasoning_content` field containing a JSON object, parse that.
On empty content retry once WITHOUT response_format before falling back. Add unit tests with fakes (no network).
Then do ONE live check (at most 4 real calls total) through `LLMClient(load_settings())` with env
ARGUS_MODELS_FAST=groq:openai/gpt-oss-20b and ARGUS_MODELS_SMART=groq:openai/gpt-oss-120b, printing tier, ok/fail, latency.
Run .venv/Scripts/python.exe -m pytest -q tests/test_llm_client.py. Report changes + the live check results.
