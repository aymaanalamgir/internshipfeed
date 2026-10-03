"""Browser smoke check against a running, populated development API."""
import argparse
from pathlib import Path

from playwright.sync_api import sync_playwright

parser = argparse.ArgumentParser()
parser.add_argument("--url", default="http://127.0.0.1:8000")
parser.add_argument("--browser-executable", help="Use an installed Chromium browser")
args = parser.parse_args()

with sync_playwright() as playwright:
    browser = playwright.chromium.launch(executable_path=args.browser_executable, headless=True)
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(args.url)
    page.wait_for_function("document.querySelector('#summary').textContent.includes(' of ')")
    all_count = page.locator("#jobs tr").count()
    assert all_count > 0
    page.select_option("#status", "open")
    assert page.locator("#jobs tr").count() < all_count
    assert all(text == "open" for text in page.locator("#jobs .badge").all_text_contents())
    page.fill("#search", "Software Engineer Co-op, Agents")
    assert page.locator("#jobs tr").count() == 1
    page.get_by_role("button", name="Software Engineer Co-op, Agents", exact=True).click()
    page.get_by_role("heading", name="Job description", exact=True).wait_for()
    assert page.locator("#detail-content").get_by_text("React", exact=True).count() == 1
    assert page.get_by_role("heading", name="Key requirements", exact=True).is_visible()
    assert len(page.locator(".description").inner_text()) > 200
    page.get_by_role("button", name="Close", exact=True).click()
    page.fill("#search", "no-such-company-1234567")
    assert page.locator("#message").is_visible()
    assert page.locator("#jobs tr").count() == 0
    page.fill("#search", "")
    page.select_option("#status", "all")
    page.get_by_role("button", name="Refresh", exact=True).click()
    page.wait_for_function("!document.querySelector('#reload').disabled")
    assert page.locator("#jobs tr").count() == all_count
    screenshots = Path("data/screenshots")
    screenshots.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(screenshots / "jobs-desktop.png"))
    page.set_viewport_size({"width": 390, "height": 844})
    page.fill("#search", "Software Engineer Co-op, Agents")
    page.get_by_role("button", name="Software Engineer Co-op, Agents", exact=True).click()
    page.get_by_role("heading", name="Key requirements", exact=True).wait_for()
    page.screenshot(path=str(screenshots / "job-mobile.png"))
    page.keyboard.press("Escape")
    assert not page.locator("#job-details").is_visible()
    assert not errors, errors
    browser.close()
    print("Browser checks passed: listings, search, status, details, requirements, refresh, mobile dialog; %s jobs" % all_count)
