"""Debug exam suite authoring."""
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
SUITE_FILE = ROOT / "exam" / "argus_suite.json"
CONTEXT_DIR = ROOT / "exam" / "context"
HOME = ROOT / ".argus-exam-debug"
BASE_URL = "http://127.0.0.1:8010"
CREDS = {"user": "reception@mediqueue.io", "password": "triage42"}


async def main():
    from argus.config import Settings
    from argus.runner.author import author as author_suite

    if HOME.exists():
        shutil.rmtree(HOME)
    HOME.mkdir(parents=True, exist_ok=True)

    suite_specs = json.loads(SUITE_FILE.read_text(encoding="utf-8"))
    
    # Only try a few tests to debug
    test_ids_to_try = ["age-validation", "book-consultation", "cancel-appointment", "reschedule-appointment", "toggle-sms-reminders"]
    selected = [s for s in suite_specs if s["id"] in test_ids_to_try]

    settings = Settings(
        home=HOME,
        base_url=BASE_URL,
        context_dir=CONTEXT_DIR,
        credentials=CREDS,
        headless=True,
        llm_enabled=False,
    )
    settings.build = "r1"

    def log_fn(msg):
        print(msg, flush=True)

    print("=== Starting authoring debug ===")
    try:
        authored = await author_suite(settings, selected, log=log_fn)
        print(f"\n=== Authored {len(authored)} tests ===")
        for t in authored:
            print(f"  {t.id}: {len(t.steps)} steps, {len(t.oracles)} oracles")
    except Exception as e:
        print(f"CRASH: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
