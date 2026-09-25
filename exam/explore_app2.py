"""Explore booking flow and settings toggle details."""
from __future__ import annotations
import asyncio
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_URL = "http://127.0.0.1:8010"
EMAIL = "reception@mediqueue.io"
PASSWORD = "triage42"


async def main() -> None:
    from playwright.async_api import async_playwright

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

        # ---- Go directly to booking via Doctors ----
        print("=== FULL BOOKING FLOW ===")
        await page.goto(f"{BASE_URL}/#/app/doctors", wait_until="networkidle")
        await page.wait_for_timeout(1000)
        # Click Book on first doctor (Dr Aarav Mehta)
        await page.click("button:has-text('Book')", timeout=3000)
        await page.wait_for_timeout(1000)

        # Step 1 - Patient details
        print("Step 1 URL:", page.url)
        print("Step 1 body:", await page.evaluate("() => document.body.innerText.slice(0,300)"))
        
        # Check ids
        ids = await page.evaluate("""() => {
            const els = Array.from(document.querySelectorAll('[id]'));
            return els.map(el => ({id: el.id, tag: el.tagName.toLowerCase(), text: el.textContent.trim().slice(0,40)}));
        }""")
        print("IDs found:", ids)

        # Fill patient details
        await page.fill("#mq-patient-name", "Test Patient")
        await page.fill("#mq-patient-age", "35")
        await page.fill("#mq-patient-phone", "+91 9876543210")
        # Click Consultation visit type
        await page.click("button:has-text('Consultation')", timeout=2000)
        await page.wait_for_timeout(300)
        # Click Next
        await page.click("#mq-booking-next")
        await page.wait_for_timeout(1000)

        # Step 2 - Slot
        print("\nStep 2 URL:", page.url)
        print("Step 2 body:", await page.evaluate("() => document.body.innerText.slice(0,400)"))
        step2_ids = await page.evaluate("""() => {
            const els = Array.from(document.querySelectorAll('[id], button, select'));
            return els.map(el => ({id: el.id || '', tag: el.tagName.toLowerCase(), text: el.textContent.trim().slice(0,50), type: el.getAttribute('type') || ''}));
        }""")
        print("Step 2 elements:", step2_ids[:20])

        # Select first slot
        slot_buttons = await page.query_selector_all("button[data-slot], [class*=slot] button, button:has-text('Today'), button:has-text('Tomorrow')")
        print("Slot buttons found:", len(slot_buttons))
        if slot_buttons:
            txt = await slot_buttons[0].text_content()
            print("First slot:", txt)
            await slot_buttons[0].click()
        await page.wait_for_timeout(300)
        # Click Next
        try:
            await page.click("#mq-booking-next", timeout=2000)
        except Exception:
            await page.click("button:has-text('Next')", timeout=2000)
        await page.wait_for_timeout(1000)

        # Step 3 - Confirm
        print("\nStep 3 URL:", page.url)
        body = await page.evaluate("() => document.body.innerText.slice(0,600)")
        print("Step 3 body:", body)
        step3_els = await page.evaluate("""() => {
            const els = Array.from(document.querySelectorAll('[id], button'));
            return els.map(el => ({id: el.id || '', tag: el.tagName.toLowerCase(), text: el.textContent.trim().slice(0,50)}));
        }""")
        print("Step 3 elements:", step3_els[:20])

        # Check fee display
        print("\nFee breakdown:")
        fee_text = await page.evaluate("""() => {
            const el = document.querySelector('[class*=fee], [class*=price], [class*=total], [class*=invoice], [class*=breakdown]');
            return el ? el.innerText : '';
        }""")
        print("Fee area:", fee_text)

        # Click Confirm
        for sel in ["button:has-text('Confirm')", "button:has-text('Book')", "#mq-booking-confirm", "button[type=submit]"]:
            try:
                await page.click(sel, timeout=2000)
                break
            except Exception:
                continue
        await page.wait_for_timeout(1500)
        print("\nAfter confirm URL:", page.url)
        body = await page.evaluate("() => document.body.innerText.slice(0,500)")
        print("After confirm body:", body)

        # ---- Settings - SMS toggle ----
        print("\n=== SETTINGS - SMS TOGGLE ===")
        await page.goto(f"{BASE_URL}/#/app/settings", wait_until="networkidle")
        await page.wait_for_timeout(500)
        body = await page.evaluate("() => document.body.innerText.slice(0,500)")
        print("Settings body:", body)
        sms_els = await page.evaluate("""() => {
            const els = Array.from(document.querySelectorAll('[id*=sms i], [class*=sms i], [role=switch], input[type=checkbox], label'));
            return els.map(el => ({id: el.id, tag: el.tagName.toLowerCase(), role: el.getAttribute('role') || '', text: el.textContent.trim().slice(0,50), checked: el.checked}));
        }""")
        print("SMS elements:", sms_els)
        html_near_sms = await page.evaluate("""() => {
            const smsEl = document.querySelector('[id*=sms], [class*=sms]');
            return smsEl ? smsEl.parentElement.innerHTML.slice(0,500) : 'not found';
        }""")
        print("HTML near SMS:", html_near_sms)

        # ---- Cancel flow - check confirmation dialog ----
        print("\n=== CANCEL APPOINTMENT FLOW ===")
        await page.goto(f"{BASE_URL}/#/app/appointments", wait_until="networkidle")
        await page.wait_for_timeout(500)
        # Click first Cancel
        await page.click("button:has-text('Cancel')", timeout=2000)
        await page.wait_for_timeout(500)
        body = await page.evaluate("() => document.body.innerText.slice(0,600)")
        print("After cancel click:", body)
        confirm_els = await page.evaluate("""() => {
            const els = Array.from(document.querySelectorAll('[role=dialog] button, [class*=modal] button, [class*=dialog] button'));
            return els.map(el => ({text: el.textContent.trim(), id: el.id}));
        }""")
        print("Confirm dialog buttons:", confirm_els)

        # ---- New appointment nav link ----
        print("\n=== NEW APPOINTMENT LINK ===")
        await page.goto(f"{BASE_URL}/#/app/home", wait_until="networkidle")
        await page.wait_for_timeout(500)
        await page.click("a:has-text('New appointment')", timeout=2000)
        await page.wait_for_timeout(1000)
        print("URL:", page.url)
        body = await page.evaluate("() => document.body.innerText.slice(0,300)")
        print("Body:", body)

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
