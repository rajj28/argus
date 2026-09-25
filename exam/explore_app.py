"""Temporary exploration script for MediQueue r1 UI structure."""
from __future__ import annotations
import asyncio
import json
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_URL = "http://127.0.0.1:8010"
EMAIL = "reception@mediqueue.io"
PASSWORD = "triage42"


async def snap_page(page) -> list[dict]:
    return await page.evaluate("""() => {
        const sel = 'input,button,a,select,textarea,[role=button],[role=link],[role=combobox],[role=switch],[role=tab],[role=checkbox]';
        const els = Array.from(document.querySelectorAll(sel));
        return els.map(el => ({
            tag: el.tagName.toLowerCase(),
            type: el.getAttribute('type') || '',
            id: el.id || '',
            name: el.getAttribute('name') || '',
            placeholder: el.getAttribute('placeholder') || '',
            text: (el.textContent || '').trim().slice(0, 60),
            ariaLabel: el.getAttribute('aria-label') || '',
            role: el.getAttribute('role') || el.tagName.toLowerCase(),
            testid: el.getAttribute('data-testid') || '',
            forAttr: el.getAttribute('for') || '',
            className: (el.className || '').slice(0, 40),
        }));
    }""")


def show_els(els: list[dict], limit: int = 30) -> None:
    for e in els[:limit]:
        parts = [f"{e['tag']}[{e['type']}]"]
        if e["id"]:
            parts.append(f"id={e['id']}")
        if e["name"]:
            parts.append(f"name={e['name']}")
        if e["placeholder"]:
            parts.append(f"ph={e['placeholder']}")
        if e["ariaLabel"]:
            parts.append(f"aria={e['ariaLabel']}")
        if e["testid"]:
            parts.append(f"testid={e['testid']}")
        if e["text"]:
            parts.append(f"text={e['text'][:40]}")
        print("  " + " | ".join(parts))


async def main() -> None:
    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(viewport={"width": 1280, "height": 800})
        page = await context.new_page()

        # ---- Login page ----
        await page.goto(f"{BASE_URL}/#/login", wait_until="networkidle")
        await page.wait_for_timeout(1000)
        print("=== LOGIN PAGE ===")
        print("URL:", page.url)
        els = await snap_page(page)
        show_els(els)
        body = await page.evaluate("() => document.body.innerText.slice(0, 300)")
        print("BODY:", body)

        # Perform login
        try:
            await page.fill("input[type=email]", EMAIL)
        except Exception:
            # try placeholder
            await page.fill("input[placeholder*='email' i], input[placeholder*='Email' i]", EMAIL)
        await page.fill("input[type=password]", PASSWORD)
        # click sign-in button
        for sel in ["button[type=submit]", "button:has-text('Sign in')", "button:has-text('Log in')",
                    "button:has-text('Login')", "button:has-text('Sign In')"]:
            try:
                await page.click(sel, timeout=2000)
                break
            except Exception:
                continue
        await page.wait_for_timeout(2000)

        print("\n=== AFTER LOGIN (overview/dashboard) ===")
        print("URL:", page.url)
        body = await page.evaluate("() => document.body.innerText.slice(0, 500)")
        print("BODY:", body)
        els = await snap_page(page)
        show_els(els, 30)

        # ---- Navigate to Doctors ----
        print("\n=== NAVIGATING TO DOCTORS ===")
        # try clicking nav links
        nav_texts = await page.evaluate("""() => {
            const links = Array.from(document.querySelectorAll('a, [role=link], nav button, [class*=nav] button'));
            return links.map(l => l.textContent.trim()).filter(t => t.length > 0);
        }""")
        print("Nav items:", nav_texts)
        for text in ["Doctors", "Doctor", "Directory", "Providers"]:
            try:
                await page.click(f"text={text}", timeout=2000)
                await page.wait_for_timeout(1500)
                break
            except Exception:
                continue
        print("URL:", page.url)
        body = await page.evaluate("() => document.body.innerText.slice(0, 600)")
        print("BODY:", body)
        els = await snap_page(page)
        show_els(els, 30)

        # ---- Appointments ----
        print("\n=== NAVIGATING TO APPOINTMENTS ===")
        for text in ["Appointments", "Schedule", "Appointment"]:
            try:
                await page.click(f"text={text}", timeout=2000)
                await page.wait_for_timeout(1500)
                break
            except Exception:
                continue
        print("URL:", page.url)
        body = await page.evaluate("() => document.body.innerText.slice(0, 600)")
        print("BODY:", body)
        els = await snap_page(page)
        show_els(els, 25)

        # Try to scroll down and see Rahul Iyer
        print("\n--- Scrolling appointments list ---")
        try:
            # Find the scrollable list container
            await page.evaluate("""() => {
                const containers = Array.from(document.querySelectorAll('[class*=list], [class*=table], [class*=scroll], tbody, [role=list], [role=grid]'));
                for (const c of containers) {
                    if (c.scrollHeight > c.clientHeight + 50) {
                        c.scrollTop = 99999;
                        return c.tagName + ' ' + c.className.slice(0,40);
                    }
                }
                window.scrollTo(0, 99999);
                return 'window';
            }""")
            await page.wait_for_timeout(500)
            body = await page.evaluate("() => document.body.innerText.slice(0, 800)")
            print("BODY after scroll:", body)
        except Exception as e:
            print("Scroll error:", e)

        # ---- Billing ----
        print("\n=== NAVIGATING TO BILLING ===")
        for text in ["Billing", "Invoice", "Finance"]:
            try:
                await page.click(f"text={text}", timeout=2000)
                await page.wait_for_timeout(1000)
                break
            except Exception:
                continue
        print("URL:", page.url)
        body = await page.evaluate("() => document.body.innerText.slice(0, 600)")
        print("BODY:", body)
        els = await snap_page(page)
        show_els(els, 20)

        # ---- Settings ----
        print("\n=== NAVIGATING TO SETTINGS ===")
        for text in ["Settings", "Preferences", "Config"]:
            try:
                await page.click(f"text={text}", timeout=2000)
                await page.wait_for_timeout(1000)
                break
            except Exception:
                continue
        print("URL:", page.url)
        body = await page.evaluate("() => document.body.innerText.slice(0, 600)")
        print("BODY:", body)
        els = await snap_page(page)
        show_els(els, 20)

        # ---- Booking flow ----
        print("\n=== BOOKING FLOW ===")
        # Navigate to Doctors again
        for text in ["Doctors", "Doctor", "Directory"]:
            try:
                await page.click(f"text={text}", timeout=2000)
                await page.wait_for_timeout(1500)
                break
            except Exception:
                continue
        await page.wait_for_timeout(1000)
        body = await page.evaluate("() => document.body.innerText.slice(0, 500)")
        print("Doctors page body:", body)
        # Try clicking Book on first doctor
        for sel in ["button:has-text('Book')", "button:has-text('Book consultation')",
                    "a:has-text('Book')", "button:has-text('Consult')"]:
            try:
                await page.click(sel, timeout=2000)
                await page.wait_for_timeout(1500)
                break
            except Exception:
                continue
        print("URL after click Book:", page.url)
        body = await page.evaluate("() => document.body.innerText.slice(0, 600)")
        print("BODY:", body)
        els = await snap_page(page)
        show_els(els, 30)

        # ---- Check the HTML templates ----
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
