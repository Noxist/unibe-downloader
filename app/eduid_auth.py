"""Create an authenticated ILIAS Playwright state using auto_reserve credentials."""
import json
import os
from pathlib import Path
import re
import sys
import time

import pyotp
from playwright.sync_api import sync_playwright

ACCOUNT = "account-1@example.invalid"
SETTINGS = Path("/home/leandro/service_data/auto_reserve/settings.json")
SOURCE_STATE = Path("/home/leandro/service_data/auto_reserve/sessions/leandro_aeschbacher77_at_gmail_com.json")
COURSE_URL = "https://ilias.unibe.ch/goto.php?target=crs_3687731"
CHROMIUM = Path("/home/leandro/services/auto_reserve/data/playwright/chromium-1091/chrome-linux/chrome")


def account():
    data = json.loads(SETTINGS.read_text(encoding="utf-8"))
    for entry in data.get("accounts", []):
        if entry.get("email", "").casefold() == ACCOUNT.casefold():
            return entry
    raise RuntimeError(f"Konto {ACCOUNT} fehlt in auto_reserve.")


def visible(page, selectors):
    for selector in selectors:
        item = page.locator(selector).first
        try:
            if item.count() and item.is_visible():
                return item
        except Exception:
            pass
    return None


def is_authenticated(page):
    if page.url.startswith("https://ilias.unibe.ch/"):
        return bool(page.locator(
            'a[href*="logout" i], .glyphicon-logout, [aria-label="Logout" i], [aria-label="Abmelden" i]'
        ).count()) and not page.locator('input[type="password"]:visible').count()
    return False


def main():
    destination = Path(os.environ["ILIAS_STORAGE_STATE"]).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    creds = account()
    with sync_playwright() as playwright:
        launch = {"headless": True}
        if CHROMIUM.is_file():
            launch["executable_path"] = str(CHROMIUM)
        browser = playwright.chromium.launch(**launch)
        context_args = {}
        if SOURCE_STATE.is_file():
            context_args["storage_state"] = str(SOURCE_STATE)
        context = browser.new_context(**context_args)
        page = context.new_page()
        try:
            page.goto(COURSE_URL, wait_until="domcontentloaded", timeout=60000)
            deadline = time.time() + 90
            submitted_user = submitted_password = submitted_mfa = False
            while time.time() < deadline:
                page.wait_for_timeout(500)
                if is_authenticated(page):
                    page.goto(COURSE_URL, wait_until="domcontentloaded", timeout=60000)
                    page.wait_for_timeout(1000)
                    if is_authenticated(page):
                        temp = destination.with_suffix(destination.suffix + ".tmp")
                        context.storage_state(path=str(temp))
                        os.chmod(temp, 0o600)
                        temp.replace(destination)
                        os.chmod(destination, 0o600)
                        print("ILIAS-Sitzung erfolgreich erneuert und Kurszugriff geprüft.")
                        return 0
                username = visible(page, ['#username', 'input[name="username"]', 'input[type="email"]'])
                if username and not submitted_user:
                    username.fill(creds["email"])
                    username.press("Enter")
                    submitted_user = True
                    continue
                password = visible(page, ['#password', 'input[name="password"]', 'input[type="password"]'])
                if password and not submitted_password:
                    password.fill(creds["password"])
                    password.press("Enter")
                    submitted_password = True
                    continue
                otp = visible(page, ['input[autocomplete="one-time-code"]', 'input[name*="otp" i]',
                    'input[name*="totp" i]', 'input[name*="mfa" i]', 'input[type="tel"]'])
                if otp and not submitted_mfa:
                    key = str(creds.get("MFAKEY", "")).replace(" ", "")
                    if not key:
                        raise RuntimeError("MFAKEY fehlt im auto_reserve-Konto.")
                    otp.fill(pyotp.TOTP(key).now())
                    otp.press("Enter")
                    submitted_mfa = True
                    continue
                consent = visible(page, ['input[name="_eventId_proceed"]', 'button[name="_eventId_proceed"]'])
                if consent:
                    consent.click()
                    continue
                wayf = visible(page, ['select#user_idp', 'select[name="user_idp"]'])
                if wayf:
                    wayf.select_option('https://aai-idp.unibe.ch/idp/shibboleth')
                    submit = visible(page, ['#wayf_submit_button', 'input[name="Login"]'])
                    if submit:
                        submit.click()
                    continue
                login = visible(page, ['a:has-text("SWITCH edu-ID")', 'button:has-text("SWITCH edu-ID")',
                    'a:has-text("Anmelden")', 'button:has-text("Anmelden")',
                    'a:has-text("Login")', 'button:has-text("Login")',
                    'a:has-text("Continue")', 'button:has-text("Continue")',
                    'input[type="submit"][value="Continue"]'])
                if login:
                    login.click()
                    continue
            host = re.sub(r"[^a-z0-9.-]", "", page.url.split("/")[2].lower()) if "://" in page.url else "unbekannt"
            path = page.url.split("?", 1)[0].replace("https://" + host, "")[:120]
            controls = page.locator('a, button, input[type="submit"]').evaluate_all(
                "els => els.filter(e => !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length))"
                ".slice(0, 20).map(e => (e.innerText || e.value || e.getAttribute('aria-label') || '').trim().slice(0, 80))"
            )
            raise RuntimeError(f"ILIAS-Anmeldung nicht abgeschlossen (Host: {host}, Pfad: {path}, sichtbare Controls: {controls}).")
        finally:
            context.close()
            browser.close()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"ILIAS-Authentifizierung fehlgeschlagen: {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(1)
