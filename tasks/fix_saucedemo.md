# Task: make the SauceDemo real-world benchmark trustworthy

Read `AGENTS.md`, `tasks/realworld_saucedemo.md`, `.logs/saucedemo.log` (last run). You may edit ONLY
`examples/saucedemo/**`, `benchmarks/realworld_saucedemo.py`. Never open .env, no git, LLM off
(`ARGUS_LLM=off`), `PYTHONIOENCODING=utf-8`.

Problems in the last run:
1. `standard_user` (the correct app) got NEEDS_REVIEW on 5 of 6 tests - the baseline itself is not green.
2. 6 tests ran although only 4 authored cleanly -> the benchmark must wipe `.argus-saucedemo` before
   authoring so stale tests never run.
3. `item-detail` / `logout` could not find their targets (SauceDemo product names are links/`div`s with
   `data-test` attributes; the logout link is inside the burger menu which must be opened first).
4. checkout tests failed their baseline.

For each NEEDS_REVIEW on standard_user, read the run report (`.argus-saucedemo/runs/<id>/report.json`:
per-step observations + test observations) and fix the SUITE (semantic `find` targets, steps, oracles,
start_url; SauceDemo keeps the cart in localStorage - each test must start from a clean cart, e.g. start at
/inventory.html and remove items first, or use `requires_login` with a fresh context). Goal: standard_user
= all PASS on two consecutive runs; then the per-user table. If you conclude Argus itself has a bug, do not
edit argus/ - write the exact observation text, test and a suggested fix in your final report.
Report the final user x test table.
