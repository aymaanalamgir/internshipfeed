"""Verify post-Apply choices with real browser/API/database state in a disposable schema."""
import argparse
import os
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path
from urllib.request import urlopen

from dotenv import load_dotenv
from playwright.sync_api import expect, sync_playwright
from psycopg import sql
from psycopg.conninfo import make_conninfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from internshipfeed import db
from internshipfeed.models import JobListing

parser = argparse.ArgumentParser()
parser.add_argument('--browser-executable')
args = parser.parse_args()
load_dotenv(ROOT / '.env', override=False)
database_url = os.environ.get('TEST_DATABASE_URL') or os.environ.get('DATABASE_URL')
if not database_url:
    raise SystemExit('Configure DATABASE_URL or TEST_DATABASE_URL first.')
schema = 'apply_ui_' + uuid.uuid4().hex
server = None
try:
    with db.connect(database_url) as admin:
        admin.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
    dsn = make_conninfo(database_url, options='-csearch_path=' + schema)
    with db.connect(dsn) as conn:
        db.initialize(conn)
        candidate = db.development_profile(conn)['id']
        source = conn.execute("SELECT * FROM job_sources WHERE key='canadian-tech'").fetchone()
        snapshot = db.save_snapshot(conn, source['id'], 'Post-Apply browser fixtures')
        ids = []
        for index in range(6):
            job = JobListing(company='Apply Fixture', title='Software Engineer Intern ' + str(index + 1), location='Toronto',
                url=None if index == 4 else 'https://applications.example.test/' + str(index + 1),
                source=source['repository'], source_id=source['id'], snapshot_id=str(snapshot['id']),
                source_line=index + 1, external_id='apply-' + str(index), status='open')
            db.save_job(conn, job)
            ids.append(db.get_source_job(conn, source['id'], job.external_id).canonical_job_id)
        first, saved, applied, failure, missing, manual = ids
        db.record_interaction(conn, candidate, saved, 'save')
        db.record_interaction(conn, candidate, applied, 'applied')
        before_jobs = conn.execute('SELECT * FROM canonical_jobs ORDER BY id').fetchall()
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            port = listener.getsockname()[1]
        base = 'http://127.0.0.1:' + str(port)
        server = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'internshipfeed.api:app', '--host', '127.0.0.1', '--port', str(port)],
            cwd=ROOT, env=dict(os.environ, DATABASE_URL=dsn), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(100):
            try:
                with urlopen(base + '/api/health', timeout=1) as response:
                    if response.status == 200:
                        break
            except OSError:
                if server.poll() is not None:
                    raise RuntimeError('Temporary API server exited during startup')
                time.sleep(0.1)
        else:
            raise RuntimeError('Temporary API did not become ready')
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=args.browser_executable, headless=True)
            try:
                context = browser.new_context(viewport={'width': 1280, 'height': 1000})
                owner=conn.execute('SELECT user_id FROM candidate_profiles WHERE id=%s',(candidate,)).fetchone()['user_id']
                context.add_cookies([{'name':'internshipfeed_session','value':str(owner),'url':base,'httpOnly':True,'sameSite':'Lax'}])
                def external(route):
                    assert 'referer' not in route.request.headers
                    route.fulfill(body='<title>Application fixture</title><p>External application</p>', content_type='text/html')
                context.route('https://applications.example.test/**', external)
                page = context.new_page()
                errors, navigations = [], []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.on('framenavigated', lambda frame: navigations.append(frame.url) if frame == page.main_frame else None)
                modal = page.get_by_role('dialog', name='Did you apply for this role?', exact=True)
                def card(job_id):
                    return page.locator('[data-job-id="%s"]' % job_id)
                def events(job_id):
                    return [row['interaction_type'] for row in conn.execute('SELECT interaction_type FROM job_interactions WHERE canonical_job_id=%s ORDER BY id', (job_id,))]
                def state(job_id):
                    return next(row for row in page.request.get(base + '/api/interactions').json() if row['job_id'] == job_id)
                def open_apply(job_id, container=None, label='Apply', wait_for_click=True):
                    container = container if container is not None else card(job_id)
                    def open_tab():
                        with page.expect_popup() as popup:
                            container.get_by_role('link', name=label, exact=True).click()
                        popup.value.wait_for_load_state()
                        assert popup.value.url.startswith('https://applications.example.test/')
                        assert popup.value.evaluate('window.opener === null')
                        popup.value.close()
                    if wait_for_click:
                        with page.expect_response(lambda r: r.url.endswith('/interactions') and r.request.method == 'POST' and r.request.post_data_json['type'] == 'click'):
                            open_tab()
                    else:
                        open_tab()
                page.goto(base + '/')
                expect(page.locator('.nav-links a')).to_have_count(5)
                assert page.locator('.nav-links a').all_text_contents() == ['Recommended', 'All listings', 'Applied', 'Saved', 'Dismissed']
                expect(page.locator('.application-status')).to_have_count(0)
                expect(card(first)).to_be_visible()
                expect(card(first).get_by_role('button', name='Mark job as applied', exact=True)).to_have_count(0)
                expect(card(saved)).to_have_count(0)
                open_apply(first)
                expect(modal).to_be_visible()
                assert events(first) == ['click']
                assert not state(first)['applied']
                initial_navigation_count = len(navigations)
                for _ in range(12):
                    page.keyboard.press('Tab')
                    assert page.evaluate("document.activeElement.closest('dialog')?.classList.contains('apply-drawer')")
                screenshots = ROOT / 'data/screenshots'
                screenshots.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(screenshots / 'apply-confirmation-desktop.png'))
                page.set_viewport_size({'width': 390, 'height': 844})
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                page.screenshot(path=str(screenshots / 'apply-confirmation-mobile.png'))
                page.set_viewport_size({'width': 1280, 'height': 1000})
                modal.get_by_role('button', name='Keep in feed', exact=True).click()
                expect(modal).to_have_count(0)
                expect(card(first)).to_be_visible()
                assert len(navigations) == initial_navigation_count and events(first) == ['click']
                for how in ('escape', 'close', 'backdrop'):
                    open_apply(first)
                    expect(modal).to_be_visible()
                    before = events(first)
                    if how == 'escape': page.keyboard.press('Escape')
                    elif how == 'close': modal.get_by_role('button', name='Close application confirmation', exact=True).click()
                    else: page.mouse.click(8, 8)
                    expect(modal).to_have_count(0)
                    assert events(first) == before
                    expect(card(first)).to_be_visible()
                open_apply(first)
                held = []
                path = '**/api/jobs/%s/interactions' % first
                page.route(path, lambda route: held.append(route))
                modal.get_by_role('button', name='Save for later', exact=True).click()
                expect(modal.get_by_role('button', name='Save for later', exact=True)).to_have_text('Saving…I want to come back to this.')
                assert not state(first)['saved']
                expect(card(first)).to_be_visible()
                held[0].continue_()
                page.unroute(path)
                expect(modal).to_have_count(0)
                expect(card(first)).to_have_count(0)
                assert events(first)[-1] == 'save'
                assert first not in [row['job']['id'] for row in page.request.get(base + '/api/recommendations').json()]
                assert first in [row['job']['id'] for row in page.request.get(base + '/api/feed').json()['items']]
                page.reload()
                expect(card(first)).to_have_count(0)
                page.get_by_role('link', name='Saved', exact=True).click()
                expect(card(first)).to_be_visible()
                expect(page.locator('.application-status')).to_have_count(0)
                open_apply(saved)
                expect(modal).to_be_visible()
                before = events(saved)
                modal.get_by_role('button', name='Save for later', exact=True).click()
                expect(modal).to_have_count(0)
                assert events(saved) == before
                card(saved).locator('.job-title').click()
                detail = page.get_by_role('dialog', name='Software Engineer Intern 2', exact=True)
                open_apply(saved, container=detail)
                expect(page.locator('dialog[open]')).to_have_count(2)
                modal.get_by_role('button', name='Keep in feed', exact=True).click()
                expect(detail).to_be_visible()
                expect(detail.get_by_role('link', name='Apply', exact=True)).to_be_focused()
                page.keyboard.press('Escape')
                open_apply(first)
                page.route(path, lambda route: route.fulfill(status=503, json={'detail': 'private diagnostics'}))
                modal.get_by_role('button', name='Yes, I applied', exact=True).click()
                expect(modal.get_by_role('alert')).to_have_text('Could not mark this job as applied. Please try again.')
                assert not state(first)['applied']
                page.unroute(path)
                modal.get_by_role('button', name='Yes, I applied', exact=True).click()
                expect(modal).to_have_count(0)
                assert state(first)['saved'] and state(first)['applied']
                page.get_by_role('link', name='Applied', exact=True).click()
                expect(card(first)).to_be_visible()
                before = events(first)
                open_apply(first, label='Open application')
                expect(modal).to_have_count(0)
                assert events(first) == before + ['click']
                original_application = next(row['application'] for row in page.request.get(base + '/api/applied').json() if row['job']['id'] == first)
                status_control = card(first).locator('#application-status-' + str(first))
                expect(status_control).to_have_value('applied')
                status_path = '**/api/jobs/%s/application' % first
                page.route(status_path, lambda route: route.fulfill(status=503, json={'detail': 'private diagnostics'}))
                status_control.select_option('interview')
                expect(card(first).get_by_role('alert')).to_have_text('Could not update status. Please try again.')
                expect(status_control).to_have_value('applied')
                page.unroute(status_path)
                history_before_progress = events(first)
                navigation_before_progress = len(navigations)
                for status in ('interview', 'rejected', 'ghosted', 'offered'):
                    with page.expect_response(lambda response: response.url.endswith('/application') and response.request.method == 'PATCH') as changed:
                        status_control.select_option(status)
                    assert changed.value.status == 200
                    expect(status_control).to_have_value(status)
                    current = changed.value.json()
                    assert current['applied_at'] == original_application['applied_at']
                    assert events(first) == history_before_progress
                    expect(card(first)).to_be_visible()
                    page.reload()
                    expect(status_control).to_have_value(status)
                    navigation_before_progress += 1
                assert len(navigations) == navigation_before_progress
                assert page.locator('.recommendation-card').first.get_attribute('data-job-id') == str(first)
                page.locator('#application-filter').select_option('offered')
                expect(page.locator('.recommendation-card')).to_have_count(1)
                expect(status_control).to_have_value('offered')
                status_control.select_option('rejected')
                expect(card(first)).to_have_count(0)
                expect(page.get_by_text('No applications with this status.', exact=True)).to_be_visible()
                page.locator('#application-filter').select_option('rejected')
                expect(status_control).to_have_value('rejected')
                page.locator('#application-filter').select_option('')
                expect(card(applied)).to_be_visible()
                card(applied).locator('#application-status-' + str(applied)).select_option('interview')
                expect(card(applied).locator('#application-status-' + str(applied))).to_have_value('interview')
                assert page.locator('.recommendation-card').first.get_attribute('data-job-id') == str(applied)
                page.screenshot(path=str(screenshots / 'application-tracker-desktop.png'), full_page=True)
                page.set_viewport_size({'width': 390, 'height': 844})
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                page.screenshot(path=str(screenshots / 'application-tracker-mobile.png'), full_page=True)
                page.set_viewport_size({'width': 1280, 'height': 1000})
                page.get_by_role('link', name='Saved', exact=True).click()
                expect(card(first)).to_be_visible()
                expect(page.locator('.application-status')).to_have_count(0)
                page.get_by_role('link', name='Recommended', exact=True).click()
                open_apply(failure)
                failure_path = '**/api/jobs/%s/interactions' % failure
                page.route(failure_path, lambda route: route.fulfill(status=503, json={'detail': 'private diagnostics'}))
                modal.get_by_role('button', name='Save for later', exact=True).click()
                expect(modal.get_by_role('alert')).to_have_text('Could not save this job. Please try again.')
                assert not state(failure)['saved']
                expect(card(failure)).to_be_visible()
                page.unroute(failure_path)
                modal.get_by_role('button', name='Save for later', exact=True).click()
                expect(modal).to_have_count(0)
                expect(card(failure)).to_have_count(0)
                manual_path = '**/api/jobs/%s/interactions' % manual
                page.route(manual_path, lambda route: route.fulfill(status=503, json={'detail': 'private diagnostics'}))
                open_apply(manual)
                expect(modal).to_be_visible()
                expect(modal.get_by_role('alert')).to_contain_text('Click could not be recorded')
                assert events(manual) == []
                modal.get_by_role('button', name='Keep in feed', exact=True).click()
                page.unroute(manual_path)
                expect(card(missing).get_by_role('link', name='Apply', exact=True)).to_have_count(0)
                expect(card(missing).get_by_role('button', name='Apply unavailable', exact=True)).to_have_count(0)
                card(manual).locator('.job-title').click()
                detail = page.get_by_role('dialog', name='Software Engineer Intern 6', exact=True)
                detail.get_by_role('button', name='Mark job as applied', exact=True).click()
                expect(card(manual)).to_have_count(0)
                expect(detail.get_by_role('button', name='Unmark job as applied', exact=True)).to_be_visible()
                assert events(manual) == ['applied']
                detail.get_by_role('button', name='Unmark job as applied', exact=True).click()
                expect(card(manual)).to_be_visible()
                page.keyboard.press('Escape')
                late = []
                def hold_click(route):
                    if route.request.post_data_json['type'] == 'click': late.append((route, route.fetch()))
                    else: route.continue_()
                page.route(manual_path, hold_click)
                open_apply(manual, wait_for_click=False)
                expect(modal).to_be_visible()
                modal.get_by_role('button', name='Yes, I applied', exact=True).click()
                expect(modal).to_have_count(0)
                expect(card(manual)).to_have_count(0)
                assert late
                late[0][0].fulfill(response=late[0][1])
                page.unroute(manual_path)
                expect(card(manual)).to_have_count(0)
                page.get_by_role('link', name='Applied', exact=True).click()
                expect(card(manual)).to_be_visible()
                assert state(manual)['applied']
                page.get_by_role('link', name='Recommended', exact=True).click()
                expect(card(missing)).to_be_visible()
                card(missing).get_by_role('button', name='Dismiss job', exact=True).click()
                expect(card(missing)).to_have_count(0)
                page.get_by_role('link', name='Dismissed', exact=True).click()
                expect(card(missing)).to_be_visible()
                expect(page.locator('.application-status')).to_have_count(0)
                card(missing).get_by_role('button', name='Restore job', exact=True).click()
                expect(card(missing)).to_have_count(0)
                assert conn.execute('SELECT * FROM canonical_jobs ORDER BY id').fetchall() == before_jobs
                assert not errors, errors
                print('Post-Apply and application tracker browser checks passed: status editing/filtering/reordering/retention, submission date preservation, navigation order, Dismissed/Restore, and native external navigation/click, all confirmation outcomes and close paths, save/applied persistence and errors, saved deduplication, backend eligibility, manual Applied, missing links, nested focus, late click receipts, desktop/mobile, and no mutation reloads.')
            finally:
                browser.close()
finally:
    if server is not None:
        server.terminate()
        try: server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=5)
    with db.connect(database_url) as admin:
        admin.execute(sql.SQL('DROP SCHEMA IF EXISTS {} CASCADE').format(sql.Identifier(schema)))
    print('Temporary Apply API/schema removed; live candidate history was not modified.')
