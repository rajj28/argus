"""Debug age-validation authoring - specifically the slot step."""
from __future__ import annotations
import asyncio
import json
import os
import sys

sys.path.insert(0, ".")
os.environ["ARGUS_LLM"] = "off"
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_URL = "http://127.0.0.1:8010"
EMAIL = "reception@mediqueue.io"
PASSWORD = "triage42"


async def main():
    from playwright.async_api import async_playwright
    from argus.browser.snapshot import take_snapshot, compact_for_llm
    from argus.runner.author import find_element

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(viewport={"width": 1280, "height": 800})
        page = await context.new_page()

        # Login
        await page.goto(f"{BASE_URL}/#/login", wait_until="networkidle")
        await page.fill("input[name=email]", EMAIL)
        await page.fill("input[type=password]", PASSWORD)
        await page.click("button[type=submit]")
        await page.wait_for_timeout(1500)

        # Navigate to doctors
        await page.click("text=Doctors")
        await page.wait_for_timeout(2000)

        # Click book
        await page.click("button:has-text('Book')", timeout=3000)
        await page.wait_for_timeout(500)

        # Fill patient details
        await page.fill("#mq-patient-name", "Age Test Patient")
        await page.fill("#mq-patient-age", "150")
        await page.fill("#mq-patient-phone", "+91 9876543210")

        # Open visit type dropdown
        await page.click(".mq-select-trigger", timeout=2000)
        await page.wait_for_timeout(200)

        # Click Consultation
        await page.click("[data-mq-option='Consultation']", timeout=2000)
        await page.wait_for_timeout(200)

        # Click Next to go to slot step
        await page.click("#mq-booking-next")
        await page.wait_for_timeout(1000)

        print("=== SLOT PAGE ===")
        print("URL:", page.url)

        # Try taking a snapshot
        try:
            snap = await take_snapshot(page)
            print("Snapshot taken successfully!")
            print(compact_for_llm(snap, limit=30, only_interactive=True))
            
            # Try find_element with context
            el = find_element(snap, {"role": "radio", "context": "choose a time"})
            print(f"\nFinding radio (context='choose a time'): {el}")
            
            # Try without context
            el2 = find_element(snap, {"role": "radio"})
            print(f"Finding radio (no context): {el2}")
            
            # Check all radio elements
            radios = [e for e in snap.elements if e.role == "radio"]
            print(f"\nAll radio elements ({len(radios)}):")
            for r in radios:
                print(f"  role={r.role} name={r.name!r} ctx={r.context!r} interactive={r.interactive}")
        except Exception as e:
            print(f"Snapshot failed: {e}")
            import traceback
            traceback.print_exc()

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
