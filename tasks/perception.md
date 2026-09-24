# Task: perception + similarity layer (`argus/browser/*`, `argus/healing/similarity.py`)

Owner: you own ONLY `argus/browser/__init__.py`, `argus/browser/snapshot.js`, `argus/browser/snapshot.py`,
`argus/browser/effects.py`, `argus/healing/__init__.py`, `argus/healing/similarity.py`, `tests/fixtures/*.html`,
`tests/test_snapshot.py`, `tests/test_effects.py`, `tests/test_similarity.py`. Read `AGENTS.md`,
`docs/SPEC.md` §2 and §3.1, and `argus/models.py` first. Implement exactly those interfaces.

This is the heart of the self-healing engine, so correctness and robustness matter more than speed of delivery:

1. `snapshot.js` + `snapshot.py` per SPEC §2.1/§2.2. Accessible-name computation must handle: `aria-labelledby`,
   `aria-label`, `<label for>`, wrapping `<label>`, placeholder, title, button innerText, `input[type=submit]`
   value, `img alt` inside links/buttons. `context` must disambiguate identical buttons in different table
   rows / cards (e.g. two "View" buttons → context "Falcon-1 DJI M350 92% Idle" vs "Hawk-7 …").
2. `effects.py` per SPEC §2.3, including `wait_for_settle` (network quiet + DOM quiet via an injected
   MutationObserver counter) and dialog auto-accept with message capture. Only same-origin fetch/xhr/document.
3. `similarity.py` per SPEC §3.1 (pure functions, rapidfuzz). Include the synonym table, hash-class stripping,
   `effective_weights`, `changed_attributes`, `compatible`, `rank`.

Tests (no network): build fixtures in `tests/fixtures/` — e.g. `v1.html` (a login form + a table with 3 rows
each with a "View" button + a wizard-like form) and `v2.html` (same page after a refactor: ids renamed, classes
hashed, testids removed, wrappers added, nav moved, "Log in"→"Sign in", "Next"→"Continue", a `button` turned
into `a role=button`). Load them with `page.set_content(...)`. Required test assertions:
* snapshot finds all controls with correct role/name/label/context; runs < 150 ms on the fixture.
* for EVERY target fingerprint taken from v1, `rank()` on the v2 snapshot puts the true counterpart first with
  score ≥ 0.72 and margin ≥ 0.12 (the row "View" buttons must map to the same row). Print a table of scores.
* identical page → top score ≥ 0.95.
* a deleted element (exists in v1, removed in v2) → best score < 0.5 (must NOT be confidently healed).
* effects: clicking a button that does `fetch('/api/x', {method:'POST'})` (use `page.route` to fulfil it with
  201) records `NetCall(method="POST", path="/api/x", status=201)`; a thrown error is captured in page_errors;
  `confirm()` is auto-accepted and recorded; url change is detected.
Tune weights/thresholds only inside `similarity.py` if the tests show a real need; explain any change.
