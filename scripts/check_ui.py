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
    assert not any(text in {'closed', 'dead'} for text in page.locator('#jobs .badge').all_text_contents())
    page.get_by_role('button', name='AI settings', exact=True).click()
    page.get_by_role('heading', name='Google AI overview', exact=True).wait_for()
    assert page.locator('#ai-key').get_attribute('type') == 'password'
    page.locator('#close-ai-settings').click()
    page.select_option("#status", "open")
    assert page.locator("#jobs tr").count() <= all_count
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
    # Exercise AI presentation and async progress with browser-local fixtures.
    # No synthetic analysis is written to the real database and no provider call occurs.
    page.set_viewport_size({"width": 1280, "height": 900})
    saved = page.request.get(args.url + '/api/jobs').json()
    sample = next(job for job in saved if job['company'] == 'Solink' and job['title'] == 'Software Engineer Co-op, Agents')
    analysis = {'overview': 'UI smoke-test overview.', 'key_requirements': [{'text': 'Computer Science enrollment', 'evidence': 'Computer Science'}], 'preferred_qualifications': [], 'tech_stack': [{'text': 'TypeScript', 'evidence': 'TypeScript'}], 'frameworks': [{'text': 'React', 'evidence': 'React'}], 'responsibilities': [{'text': 'Develop software', 'evidence': 'software'}]}
    import json
    page.route('**/api/jobs/' + str(sample['id']), lambda route: route.fulfill(json=dict(sample, ai_analysis=analysis, ai_status='success')))
    page.get_by_role('button', name='Software Engineer Co-op, Agents', exact=True).click()
    page.get_by_role('heading', name='AI overview', exact=True).wait_for()
    assert page.get_by_role('heading', name='Responsibilities', exact=True).is_visible()
    page.locator('#detail-content summary').first.click()
    assert page.locator('#detail-content details').first.get_attribute('open') is not None
    page.keyboard.press('Escape')
    page.unroute('**/api/jobs/' + str(sample['id']))
    run = {'id': '00000000-0000-0000-0000-000000000001', 'status': 'running', 'scraped': 2, 'processed': 0, 'inserted': 0, 'updated': 0, 'skipped_duplicates': 0, 'failed': 0}
    polls = [0]
    def progress(route):
        polls[0] += 1
        report = run if polls[0] == 1 else dict(run, status='success', processed=2)
        route.fulfill(json=report)
    page.route('**/api/sync', lambda route: route.fulfill(status=202, json=run))
    page.route('**/api/ingestion-runs/latest', progress)
    page.locator('#sync-jobs').click()
    page.wait_for_function("document.querySelector('#sync-status').textContent.startsWith('Reading descriptions:')")
    page.wait_for_function("document.querySelector('#sync-status').textContent === 'Sync complete.'")
    assert not page.locator('#sync-jobs').is_disabled()
    page.unroute('**/api/sync')
    page.unroute('**/api/ingestion-runs/latest')
    assert not errors, errors
    browser.close()
    print("Browser checks passed: listings, search, status, details, requirements, refresh, mobile dialog, AI presentation and sync progress; %s jobs" % all_count)
