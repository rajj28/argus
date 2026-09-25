"""Debug by manually reproducing record_baseline for book-consultation."""
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
HOME = ROOT / ".argus-exam-debug3"
BASE_URL = "http://127.0.0.1:8010"
CREDS = {"user": "reception@mediqueue.io", "password": "triage42"}


async def main():
    from argus.config import Settings
    from argus.runner.runner import RunContext, _execute, _act, compare_effects, resolve
    from argus.runner.author import author as author_suite, spec_from_dict
    from argus.browser.snapshot import take_snapshot
    from argus.browser.effects import EffectRecorder, wait_for_settle
    from argus.runner.runner import StepExpect
    from argus.memory.store import Memory
    from playwright.async_api import async_playwright

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

    memory = Memory(settings.home)
    run_id, run_dir = memory.new_run_dir()
    
    BOOK_TEST = {
        "id": "book-consultation",
        "name": "Book a consultation",
        "tags": ["critical"],
        "requires_login": False,
        "goal": "Book consultation",
        "start_url": "/#/login",
        "steps": [
            {"intent": "Enter email", "action": "fill", "find": {"role": "textbox", "name": "email"}, "value": "${creds.user}"},
            {"intent": "Enter password", "action": "fill", "find": {"type": "password"}, "value": "${creds.password}"},
            {"intent": "Sign in", "action": "click", "find": {"role": "button", "name": "sign in"}},
            {"intent": "Wait", "action": "wait", "value": "1500"},
            {"intent": "Click Doctors", "action": "click", "find": {"role": "link", "name": "doctors"}},
            {"intent": "Wait", "action": "wait", "value": "2000"},
            {"intent": "Click Book", "action": "click", "find": {"role": "button", "name": "book", "context": "dr. aarav mehta"}},
            {"intent": "Wait", "action": "wait", "value": "500"},
            {"intent": "Fill name", "action": "fill", "find": {"role": "textbox", "name": "patient name"}, "value": "Exam Patient"},
            {"intent": "Fill age", "action": "fill", "find": {"role": "textbox", "name": "age"}, "value": "35"},
            {"intent": "Fill phone", "action": "fill", "find": {"role": "textbox", "name": "phone"}, "value": "+91 9876543210"},
            {"intent": "Open visit type", "action": "click", "find": {"role": "button", "name": "choose visit type"}},
            {"intent": "Select Consultation", "action": "click", "find": {"role": "option", "name": "consultation"}},
            {"intent": "Click Next (slot)", "action": "click", "find": {"role": "button", "name": "next"}},
            {"intent": "Wait", "action": "wait", "value": "500"},
            {"intent": "Select slot", "action": "click", "find": {"role": "radio", "context": "choose a time"}},
            {"intent": "Click Next (confirm)", "action": "click", "find": {"role": "button", "name": "next"}},
            {"intent": "Wait", "action": "wait", "value": "500"},
            {"intent": "Confirm", "action": "click", "find": {"role": "button", "name": "confirm booking"}},
            {"intent": "Wait", "action": "wait", "value": "1500"},
        ],
        "oracles": [
            {"kind": "network_called", "params": {"method": "POST", "path": "/api/appointments", "status_class": "2xx"}, "description": "Created"},
        ]
    }

    # First author the test to get fingerprints
    print("=== Authoring to get fingerprints ===")
    authored = await author_suite(settings, [BOOK_TEST], log=print)
    
    if not authored:
        print("Author phase failed - test steps could not be resolved")
        return
    
    spec = authored[0]
    print(f"\n=== Got spec with {len(spec.steps)} steps ===")
    
    # Now manually replay through record_baseline logic
    print("\n=== Manual baseline replay ===")
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        ctx = RunContext(settings, memory, None, run_id, run_dir)
        
        from argus.runner.runner import _Exec, record_baseline
        
        context = await browser.new_context(viewport=settings.viewport)
        page = await context.new_page()
        rec = EffectRecorder(page)
        
        url = BASE_URL.rstrip("/") + "/" + spec.start_url.lstrip("/")
        print(f"Navigating to: {url}")
        await page.goto(url, wait_until="domcontentloaded", timeout=15000)
        await wait_for_settle(page, rec)
        
        st = _Exec(spec)
        
        for i, step in enumerate(spec.steps):
            print(f"\nStep {i+1}/{len(spec.steps)}: {step.intent[:50]}")
            print(f"  action={step.action}, target={step.target.describe() if step.target else None}")
            
            if step.action in ("goto", "wait") or step.target is None:
                if step.action == "wait":
                    await page.wait_for_timeout(int(step.value or 500))
                print(f"  -> skipped (wait/goto)")
                st.trace.append((step, "orig"))
                continue
            
            try:
                snap = await take_snapshot(page)
                print(f"  -> snapshot ok, {len(snap.elements)} elements")
            except Exception as e:
                print(f"  -> SNAPSHOT CRASHED: {e}")
                break
            
            from argus.healing.resolver import resolve
            res = await resolve(snap, step, ctx.weights, ctx.llm, ctx.settings)
            if not res.found:
                print(f"  -> RESOLVE FAILED (element not found)")
                break
            
            print(f"  -> resolved: {res.element.role} '{res.element.name[:30]}' (tier={res.tier})")
            
            from argus.models import StepExpect
            sr = await _execute(ctx, page, rec, st, step.model_copy(update={"expect": StepExpect()}), res, snap, "orig")
            print(f"  -> status={sr.status}, obs={[o.kind for o in sr.observations]}")
            
            if sr.status == "failed":
                print(f"  -> FAILED: {[o.detail for o in sr.observations if o.kind in ('timeout',)]}")
                break
            
            net_errors = [o for o in sr.observations if o.kind in ("page_error", "network_error")]
            if net_errors:
                print(f"  -> NET/PAGE ERROR: {[o.detail for o in net_errors]}")
                break
        
        await context.close()
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
