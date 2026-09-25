"""Take Argus snapshots at key points in the MediQueue booking flow."""
from __future__ import annotations
import asyncio
import sys
import os

sys.path.insert(0, ".")
os.environ["ARGUS_LLM"] = "off"
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_URL = "http://127.0.0.1:8010"
EMAIL = "reception@mediqueue.io"
PASSWORD = "triage42"


async def main() -> None:
    from playwright.async_api import async_playwright
    from argus.browser.snapshot import take_snapshot, compact_for_llm

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

        # Snapshot of main nav
        print("=== HOME SNAPSHOT ===")
        snap = await take_snapshot(page)
        print(compact_for_llm(snap, limit=30, only_interactive=True))

        # Doctors page
        await page.goto(f"{BASE_URL}/#/app/doctors", wait_until="networkidle")
        await page.wait_for_timeout(2000)
        print("\n=== DOCTORS PAGE SNAPSHOT ===")
        snap = await take_snapshot(page)
        print(compact_for_llm(snap, limit=30, only_interactive=True))

        # Booking - step 1
        await page.click("button:has-text('Book')", timeout=3000)
        await page.wait_for_timeout(500)
        print("\n=== BOOKING STEP 1 SNAPSHOT ===")
        snap = await take_snapshot(page)
        print(compact_for_llm(snap, limit=40, only_interactive=True))

        # Fill patient details and open visit type
        await page.fill("#mq-patient-name", "Snap Patient")
        await page.fill("#mq-patient-age", "30")
        await page.fill("#mq-patient-phone", "+91 9876543210")
        # Open the visit type dropdown
        await page.click(".mq-select-trigger", timeout=2000)
        await page.wait_for_timeout(200)
        print("\n=== BOOKING STEP 1 WITH DROPDOWN OPEN ===")
        snap = await take_snapshot(page)
        print(compact_for_llm(snap, limit=40, only_interactive=True))

        # Click Consultation
        await page.click("[data-mq-option='Consultation']", timeout=2000)
        await page.wait_for_timeout(200)
        # Go to step 2
        await page.click("#mq-booking-next")
        await page.wait_for_timeout(1000)
        print("\n=== BOOKING STEP 2 (SLOTS) SNAPSHOT ===")
        snap = await take_snapshot(page)
        print(compact_for_llm(snap, limit=30, only_interactive=True))

        # Select first slot and go to step 3
        await page.click("label.mq-slot", timeout=2000)
        await page.wait_for_timeout(200)
        await page.click("#mq-booking-next")
        await page.wait_for_timeout(1000)
        print("\n=== BOOKING STEP 3 (CONFIRM) SNAPSHOT ===")
        snap = await take_snapshot(page)
        print(compact_for_llm(snap, limit=30, only_interactive=True))

        # Settings page - SMS toggle
        await page.goto(f"{BASE_URL}/#/app/settings", wait_until="networkidle")
        await page.wait_for_timeout(500)
        print("\n=== SETTINGS PAGE SNAPSHOT ===")
        snap = await take_snapshot(page)
        print(compact_for_llm(snap, limit=30, only_interactive=True))
        
        # Show all elements (including non-interactive) related to SMS
        print("\nAll elements in settings:")
        for e in snap.elements:
            print(f"  [{e.ref}] {e.role} name={e.name!r} text={e.text!r} label={e.label!r} ctx={e.context!r} interactive={e.interactive}")

        # Appointments page
        await page.goto(f"{BASE_URL}/#/app/appointments", wait_until="networkidle")
        await page.wait_for_timeout(500)
        print("\n=== APPOINTMENTS PAGE SNAPSHOT ===")
        snap = await take_snapshot(page)
        print(compact_for_llm(snap, limit=20, only_interactive=True))
        
        # Click cancel
        await page.click("button:has-text('Cancel')", timeout=2000)
        await page.wait_for_timeout(500)
        print("\n=== CANCEL CONFIRMATION DIALOG SNAPSHOT ===")
        snap = await take_snapshot(page)
        print(compact_for_llm(snap, limit=20, only_interactive=True))

        # Click keep and try reschedule
        await page.click("[data-modal-close]", timeout=2000)
        await page.wait_for_timeout(300)
        await page.click("button:has-text('Reschedule')", timeout=2000)
        await page.wait_for_timeout(500)
        print("\n=== RESCHEDULE MODAL SNAPSHOT ===")
        snap = await take_snapshot(page)
        print(compact_for_llm(snap, limit=20, only_interactive=True))

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
