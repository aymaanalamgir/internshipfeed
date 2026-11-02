"""Browser smoke check for the combined, database-backed jobs feed."""
import argparse
from pathlib import Path
from playwright.sync_api import sync_playwright

parser = argparse.ArgumentParser()
parser.add_argument('--url', default='http://127.0.0.1:8000')
parser.add_argument('--browser-executable')
args = parser.parse_args()

with sync_playwright() as playwright:
    browser = playwright.chromium.launch(executable_path=args.browser_executable, headless=True)
    page = browser.new_page(viewport={'width': 1280, 'height': 900})
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto(args.url)
    page.wait_for_function("document.querySelector('#summary').textContent.includes(' of ')")
    page.wait_for_function("document.querySelector('#source').options.length === 3")
    saved = page.request.get(args.url + '/api/jobs').json()
    sources = page.request.get(args.url + '/api/sources').json()
    all_count = len(saved)
    assert page.locator('#jobs tr').count() == all_count > 0
    assert not any(job['status'] in {'closed', 'dead'} for job in saved)
    for source in sources:
        page.select_option('#source', source['url'])
        expected = sum(job['source'] == source['url'] for job in saved)
        assert page.locator('#jobs tr').count() == expected
    page.select_option('#source', 'all')
    page.select_option('#status', 'open')
    assert all(text == 'open' for text in page.locator('#jobs .badge').all_text_contents())
    page.select_option('#status', 'all')
    sample = saved[0]
    page.fill('#search', sample['title'])
    page.get_by_role('button', name=sample['title'], exact=True).first.click()
    page.get_by_role('heading', name='Job description', exact=True).wait_for()
    assert page.locator('#detail-content a').first.get_attribute('href') == sample['source']
    page.keyboard.press('Escape')
    page.fill('#search', 'no-such-company-1234567')
    assert page.locator('#message').is_visible()
    assert page.locator('#jobs tr').count() == 0
    page.fill('#search', '')
    page.get_by_role('button', name='Refresh', exact=True).click()
    page.wait_for_function("!document.querySelector('#reload').disabled")
    assert page.locator('#jobs tr').count() == all_count
    screenshots = Path('data/screenshots')
    screenshots.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(screenshots / 'jobs-desktop.png'))
    page.set_viewport_size({'width': 390, 'height': 844})
    page.fill('#search', sample['title'])
    page.get_by_role('button', name=sample['title'], exact=True).first.click()
    page.get_by_role('heading', name='Job description', exact=True).wait_for()
    page.screenshot(path=str(screenshots / 'job-mobile.png'))
    page.keyboard.press('Escape')
    # Browser-local progress fixture; does not start external scraping or mutate jobs.
    run = {'id': '00000000-0000-0000-0000-000000000001', 'status': 'running', 'scraped': 2, 'processed': 0, 'inserted': 0, 'updated': 0, 'skipped_duplicates': 0, 'failed': 0, 'current_source': 'Simplify Internships'}
    polls = [0]
    def progress(route):
        polls[0] += 1
        route.fulfill(json=run if polls[0] == 1 else dict(run, status='success', processed=2))
    page.route('**/api/sync', lambda route: route.fulfill(status=202, json=run))
    page.route('**/api/ingestion-runs/latest', progress)
    page.locator('#sync-jobs').click()
    page.wait_for_function("document.querySelector('#sync-status').textContent.startsWith('Processing ')")
    page.wait_for_function("document.querySelector('#sync-status').textContent === 'Sync complete.'")
    assert not page.locator('#sync-jobs').is_disabled()
    assert not errors, errors
    browser.close()
    print('Browser checks passed: combined feed, source/status filters, details, search, refresh, mobile layout, and sync progress; %s jobs' % all_count)
