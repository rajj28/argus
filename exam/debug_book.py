"""Debug book-consultation specifically."""
from __future__ import annotations
import asyncio
import json
import os
import shutil
import sys

sys.path.insert(0, ".")
os.environ["ARGUS_LLM"] = "off"
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from pathlib import Path

ROOT = Path(".")
CONTEXT_DIR = ROOT / "exam" / "context"
HOME = ROOT / ".argus-exam-debug2"
BASE_URL = "http://127.0.0.1:8010"
CREDS = {"user": "reception@mediqueue.io", "password": "triage42"}

BOOK_TEST = {
    "id": "book-consultation",
    "name": "Book a consultation",
    "tags": ["critical"],
    "requires_login": False,
    "goal": "Book a consultation and verify in appointments",
    "start_url": "/#/login",
    "steps": [
        {"intent": "Enter email", "action": "fill", "find": {"role": "textbox", "name": "email"}, "value": "${creds.user}"},
        {"intent": "Enter password", "action": "fill", "find": {"type": "password"}, "value": "${creds.password}"},
        {"intent": "Sign in", "action": "click", "find": {"role": "button", "name": "sign in"}},
        {"intent": "Wait for overview", "action": "wait", "value": "1500"},
        {"intent": "Click Doctors", "action": "click", "find": {"role": "link", "name": "doctors"}},
        {"intent": "Wait for doctors", "action": "wait", "value": "2000"},
        {"intent": "Click Book", "action": "click", "find": {"role": "button", "name": "book", "context": "dr. aarav mehta"}},
        {"intent": "Wait for wizard", "action": "wait", "value": "500"},
        {"intent": "Fill patient name", "action": "fill", "find": {"role": "textbox", "name": "patient name"}, "value": "Exam Patient", "save_as": "patient_name"},
        {"intent": "Fill age", "action": "fill", "find": {"role": "textbox", "name": "age"}, "value": "35"},
        {"intent": "Fill phone", "action": "fill", "find": {"role": "textbox", "name": "phone"}, "value": "+91 9876543210"},
        {"intent": "Open visit type", "action": "click", "find": {"role": "button", "name": "choose visit type"}},
        {"intent": "Select Consultation", "action": "click", "find": {"role": "option", "name": "consultation"}},
        {"intent": "Click Next (slot)", "action": "click", "find": {"role": "button", "name": "next"}},
        {"intent": "Wait for slots", "action": "wait", "value": "500"},
        {"intent": "Select slot", "action": "click", "find": {"role": "radio", "context": "choose a time"}},
        {"intent": "Click Next (confirm)", "action": "click", "find": {"role": "button", "name": "next"}},
        {"intent": "Wait for confirm page", "action": "wait", "value": "500"},
        {"intent": "Confirm booking", "action": "click", "find": {"role": "button", "name": "confirm booking"}},
        {"intent": "Wait for redirect", "action": "wait", "value": "1500"},
    ],
    "oracles": [
        {"kind": "network_called", "params": {"method": "POST", "path": "/api/appointments", "status_class": "2xx"}, "description": "Appointment created"},
        {"kind": "url_matches", "params": {"pattern": "app/appointments"}, "description": "On appointments page"},
    ]
}


async def main():
    from argus.config import Settings
    from argus.runner.author import author as author_suite

    if HOME.exists():
        shutil.rmtree(HOME)
    HOME.mkdir(parents=True, exist_ok=True)

    settings = Settings(
        home=HOME,
        base_url=BASE_URL,
        context_dir=CONTEXT_DIR,
        credentials=CREDS,
        headless=True,
        llm_enabled=False,
    )
    settings.build = "r1"

    print("=== Authoring book-consultation ===")
    try:
        authored = await author_suite(settings, [BOOK_TEST], log=print)
        print(f"\n=== Done: {len(authored)} tests ===")
        for t in authored:
            print(f"  {t.id}: {len(t.steps)} steps, {len(t.oracles)} oracles")
            for s in t.steps:
                print(f"    step {s.id}: {s.intent} ({s.action})")
    except Exception as e:
        print(f"CRASH: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
