# Task: blind exam harness - score Argus on the hold-out app `exam_app/` (MediQueue)

Read `AGENTS.md`, `exam/README.md`, `exam/intents.json`, `README.md` (Argus CLI). Own ONLY `benchmarks/exam.py`,
`exam/argus_suite.json`, `exam/results/**`. Do NOT edit argus/ or exam_app/ or the answer key. Never open .env; no git.
Low memory: one browser at a time; the only server you may start is `exam_app.server --port 8010` (stop it at the end).

Blindness rule: Argus may only be given base_url, credentials, `context_dir=exam/context` (PRODUCT.md + CHANGELOG.md).
`exam/answer_key.json` is read ONLY by the scorer after runs finish.

1. Human-intent suite: as a manual QA would, translate `exam/intents.json` into `exam/argus_suite.json` (Argus semantic
   authoring format - see examples/skyops_suite.json for the format ONLY) by looking at r1 in a browser. One test per
   intent id, same ids. Business oracles per intent (e.g. invoice-total-correct: text_visible of the correct total;
   book-consultation: network_called POST /api/appointments 2xx + appointment visible; age-validation: error visible +
   network_absent). Use `wait` steps where the app loads async.
2. `benchmarks/exam.py [--llm]` runs, with a fresh home `.argus-exam`:
   A. Mode "human-intent": init -> release r1 -> author argus_suite.json -> run --build r1 -> for r in r2..r6: release r ->
      run --build r (update=True). Then r2 memory probe: release r2 again, run twice more (--build r2): record heals.
   B. Mode "zero-knowledge" (only with --llm): fresh home `.argus-exam-gen`, release r1, `argus explore`, `argus generate`,
      then run r2..r6. Map generated tests to intents by name similarity (rapidfuzz) for scoring; report coverage.
   Score against `exam/answer_key.json`: per release x intent -> PASS if verdict in acceptable set. Output
   `exam/results/scorecard.md` + `.json` with: overall accuracy; per criterion (Reliability = r2 + memory probe;
   Context = r3,r4,r5,r6; Cost = LLM calls + tokens per run and total; Memory = heals on repeated runs; Generation =
   mode B coverage + baseline success); a FAILURE LIST with each wrong verdict, the observation text and the likely
   Argus weakness (e.g. iframe/shadow DOM/custom dropdown/overlay/virtualized list/paraphrased notes/UI-lies bug).
3. Run mode A with LLM off first, then with --llm (keys already in .env; budget <= 60 LLM calls). Report both scorecards
   honestly - the goal is to FIND WEAKNESSES, not to pass.
