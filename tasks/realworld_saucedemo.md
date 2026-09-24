# Task: real-world benchmark on SauceDemo (https://www.saucedemo.com)

Owner: you own ONLY `examples/saucedemo/**` and `benchmarks/realworld_saucedemo.py`. Read `AGENTS.md`,
`README.md`, `argus/runner/author.py` (semantic authoring format), `examples/skyops_suite.json` (example suite),
`argus/runner/runner.py` (`run_suite`), `argus/config.py`.

SauceDemo is a public practice shop built for test automation. Users (password `secret_sauce`):
`standard_user` (correct app), `problem_user` (broken images, sort/add-to-cart/checkout form bugs),
`error_user` (actions throw / checkout breaks), `performance_glitch_user` (slow login), `visual_user` (visual diffs).
We treat each non-standard user as a "new release" of the same product and measure whether Argus flags real bugs
while never flagging the correct build.

1. `examples/saucedemo/context/PRODUCT.md`: product description + business rules with ids, e.g. R1 login with
   valid creds reaches Products; R2 sorting "Price (low to high)" orders prices ascending; R3 adding an item shows
   cart badge count; R4 cart lists the added item with its price; R5 checkout requires first name, last name, zip;
   R6 overview total = item total + tax; R7 "Finish" shows "Thank you for your order!"; R8 product images render.
   `examples/saucedemo/context/CHANGELOG.md`: "1.0 - initial release" only.
2. `examples/saucedemo/suite.json`: 6-8 tests in the same semantic format as `examples/skyops_suite.json`
   (login [auth, requires_login false], sort by price, add to cart + badge, cart contents, checkout happy path to
   "Thank you for your order!", checkout validation negative test (missing zip -> error text), item detail page,
   logout via burger menu). Use `${creds.user}` / `${creds.password}`. Prefer oracles on business outcomes
   (text_visible, url_matches). Keep steps human-semantic (role + name + context).
3. `benchmarks/realworld_saucedemo.py` (runnable: `.venv/Scripts/python.exe benchmarks/realworld_saucedemo.py [--llm]`):
   - uses memory home `.argus-saucedemo`, base_url `https://www.saucedemo.com`, context dir above, LLM off
     unless `--llm`;
   - authors + baselines the suite as `standard_user` (via `argus.runner.author.author`), then runs the suite
     (update=False) once per user: standard_user, problem_user, error_user, performance_glitch_user, visual_user
     (switch `settings.credentials`), printing a table user x test -> verdict and saving
     `.argus-saucedemo/realworld.json` with per-user verdict counts, LLM calls and durations.
   - Expected: standard_user all PASS/COSMETIC_DRIFT; problem_user and error_user produce BUG verdicts with
     evidence; performance_glitch_user should PASS (slow is not broken).
4. Run it (LLM off), fix suite/authoring issues until the baseline authors cleanly and the table is produced.
   If Argus itself has a genuine bug, do NOT edit argus/ - describe it precisely in your final summary
   (file, function, failing input, suggested fix). Print the final table.
