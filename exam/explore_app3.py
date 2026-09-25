"""Explore visit type dropdown structure and SMS toggle."""
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

        # Navigate to booking
        await page.goto(f"{BASE_URL}/#/app/doctors", wait_until="networkidle")
        await page.wait_for_timeout(1000)
        await page.click("button:has-text('Book')", timeout=3000)
        await page.wait_for_timeout(1000)

        # Fill patient details
        await page.fill("#mq-patient-name", "Test Patient")
        await page.fill("#mq-patient-age", "35")
        await page.fill("#mq-patient-phone", "+91 9876543210")

        # Get the visit type dropdown HTML
        html = await page.evaluate("""() => {
            const containers = document.querySelectorAll('[data-mq-option], [class*=visit], [class*=type], [class*=option], button[role=option]');
            return Array.from(containers).map(el => ({
                tag: el.tagName.toLowerCase(),
                id: el.id || '',
                role: el.getAttribute('role') || '',
                text: el.textContent.trim().slice(0,60),
                cls: (el.className || '').slice(0,60),
                dataset: JSON.stringify(el.dataset),
                ariaSelected: el.getAttribute('aria-selected'),
                visible: el.offsetHeight > 0 && el.offsetWidth > 0
            }));
        }""")
        print("Visit type options:", html)

        # Find the combobox / select trigger
        combo = await page.evaluate("""() => {
            const els = Array.from(document.querySelectorAll('[role=combobox], [role=listbox], [class*=select], [class*=combobox], [class*=dropdown]'));
            return els.map(el => ({tag: el.tagName, id: el.id, cls: el.className.slice(0,60), role: el.getAttribute('role'), text: el.textContent.trim().slice(0,60)}));
        }""")
        print("Combobox elements:", combo)

        # Click the visit type trigger button (the "Choose visit type" button)
        await page.click("button:has-text('Choose visit type')", timeout=2000)
        await page.wait_for_timeout(300)
        
        # Now check if options are visible
        html2 = await page.evaluate("""() => {
            const containers = document.querySelectorAll('[data-mq-option]');
            return Array.from(containers).map(el => ({
                text: el.textContent.trim(),
                visible: el.offsetHeight > 0 && el.offsetWidth > 0
            }));
        }""")
        print("Options after click trigger:", html2)

        # Click Consultation
        await page.click("[data-mq-option='Consultation']", timeout=2000)
        await page.wait_for_timeout(300)

        # What does the visit type button now look like?
        btn = await page.evaluate("() => document.querySelector('button:has([data-mq-option])') ? document.querySelector('[data-mq-option]').closest('button').textContent.trim() : 'n/a'")
        print("Visit type button text after select:", btn)

        # Actually get the select trigger button
        trigger = await page.evaluate("""() => {
            const btns = Array.from(document.querySelectorAll('button'));
            return btns.map(b => b.textContent.trim()).filter(t => ['Consultation','Follow-up','Emergency','Choose visit type'].includes(t));
        }""")
        print("Visit type trigger buttons:", trigger)

        # Click Next
        await page.click("#mq-booking-next")
        await page.wait_for_timeout(1000)

        # Step 2 - Slot selection
        print("\n=== STEP 2 - SLOT SELECTION ===")
        body = await page.evaluate("() => document.body.innerText.slice(0,600)")
        print("Body:", body)
        ids = await page.evaluate("""() => {
            return Array.from(document.querySelectorAll('[id]')).map(el => ({id: el.id, tag: el.tagName.toLowerCase(), text: el.textContent.trim().slice(0,40)}));
        }""")
        print("IDs:", ids)

        # Find slot radio buttons or time pickers
        slot_html = await page.evaluate("""() => {
            const els = document.querySelectorAll('[data-slot], [class*=slot], input[type=radio], [role=radio], [class*=time]');
            return Array.from(els).map(el => ({tag: el.tagName.toLowerCase(), id: el.id, cls: (el.className||'').slice(0,50), text: el.textContent.trim().slice(0,50), dataset: JSON.stringify(el.dataset), visible: el.offsetHeight > 0}));
        }""")
        print("Slot elements:", slot_html)

        # Try clicking the first slot button
        slot_btns = await page.query_selector_all("[data-slot], [class*=slot] button, button[class*=slot]")
        print(f"Slot buttons: {len(slot_btns)}")
        if slot_btns:
            txt = await slot_btns[0].text_content()
            print("First slot text:", txt)
            await slot_btns[0].click()

        # Check if there are radio inputs with slot values
        slot_inputs = await page.evaluate("""() => {
            const els = document.querySelectorAll('input[type=radio]');
            return Array.from(els).map(el => ({id: el.id, name: el.name, value: el.value, label: el.nextSibling ? el.nextSibling.textContent : ''}));
        }""")
        print("Slot radio inputs:", slot_inputs)

        # Check if there is a select element for slots
        select_els = await page.evaluate("""() => {
            const els = document.querySelectorAll('select');
            return Array.from(els).map(el => ({id: el.id, name: el.name, options: Array.from(el.options).map(o => o.text)}));
        }""")
        print("Select elements:", select_els)

        # ---- SMS toggle ----
        print("\n=== SETTINGS - SMS TOGGLE ===")
        await page.goto(f"{BASE_URL}/#/app/settings", wait_until="networkidle")
        await page.wait_for_timeout(500)
        
        # Full HTML of settings page for the sms section
        sms_html = await page.evaluate("""() => {
            const section = document.querySelector('[class*=pref], [class*=settings], main section, #mq-content');
            return section ? section.innerHTML.slice(0, 2000) : document.body.innerHTML.slice(0, 2000);
        }""")
        print("Settings HTML:", sms_html)
        
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
