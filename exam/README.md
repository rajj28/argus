# MediQueue hold-out exam

MediQueue is a self-contained FastAPI and Jinja2 clinic operations SPA used for a blind UI-testing evaluation. All state is in memory and resets on release changes.

## Run

```powershell
.venv/Scripts/python.exe -m exam_app.server --port 8010
```

Open `http://127.0.0.1:8010/#/login` and use:

- Email: `reception@mediqueue.io`
- Password: `triage42`

Stop the process with `Ctrl+C` when finished.

## Control the exam

Set a release through the private control endpoint:

```powershell
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8010/__exam/release -ContentType application/json -Body '{"release":"r3"}'
```

Reset data without changing releases:

```powershell
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8010/__exam/reset
```

Inspect release, doctor-call count, appointment count, and settings with `GET /__exam/state`. The command-line release helper resets its process-local store and rewrites `exam/context/CHANGELOG.md`:

```powershell
.venv/Scripts/python.exe -m exam_app.release r3
```

## Evaluation surface

`exam/intents.json` contains nine selector-free manual journeys. `exam/answer_key.json` maps each journey to an applicable product rule and supplies release-aware acceptable verdicts. The app rules are documented in `exam/context/PRODUCT.md`.

## Deliberate traps

- **r2:** The design refresh changes every screen class and identifier, reorders doctors, and changes booking action wording without changing behavior.
- **r3:** Booking adds a required referral choice, reverses the first two stages, moves rescheduling into an overflow menu, and shows a click-blocking introduction once per browser session.
- **r4:** Release notes are intentionally empty. Cancellation quietly moves into the same overflow menu while retaining confirmation and row removal.
- **r5:** Four defects are hidden behind a plausible performance release: age 150 is accepted, the booking interface lies when saving fails, emergency visits cost INR 500, and the invoice total is one rupee short.
- **r6:** Settings are intentionally removed. Every third doctor-directory request takes 3.5 seconds because of simulated infrastructure delay; eventual successful loading must not be classified as a product defect.

The appointment list renders only the visible window of 60 seeded rows. Rahul Iyer is deliberately near the bottom so a smoke test must scroll before interacting with the row.
