# AGENTS.md — conventions for every coding agent working in this repo

Project: **Argus**, an autonomous, self-healing, cost-aware UI testing agent (hackathon: "The Tireless Hand").
Architecture + interfaces: `docs/SPEC.md`. Data contracts: `argus/models.py` (read-only for you).

1. Work ONLY on the files your task names. Do not refactor or reformat other files.
2. Python 3.11 venv at `.venv`. Run things with `.venv/Scripts/python.exe` (Windows). Deps already installed:
   playwright (+chromium), fastapi, uvicorn, pydantic v2, openai, typer, jinja2, rapidfuzz, mcp, pytest,
   pytest-asyncio, httpx, rich. Do not install anything else without a strong reason.
3. NEVER open, print, copy or commit `.env` or any API key. Tests must not need network or an LLM.
4. Style: typed, small functions, short docstrings, `pathlib`, `encoding="utf-8"`. Match `argus/models.py`.
5. Before finishing: run `.venv/Scripts/python.exe -m pytest -q <your tests>` and make them pass.
6. End with a concise summary: files created, public functions, how to run, known limitations.
