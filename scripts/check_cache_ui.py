"""Exercise cached navigation, loading, refresh errors, mutations, and dates in an isolated browser/database."""
import os
import socket
import subprocess
import sys
import time
import uuid
from collections import Counter
from datetime import date
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import urlopen

from dotenv import load_dotenv
from playwright.sync_api import expect, sync_playwright
from psycopg import sql
from psycopg.conninfo import make_conninfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from internshipfeed import db
from internshipfeed.models import JobListing

load_dotenv(ROOT / '.env', override=False)
database_url = os.environ.get('TEST_DATABASE_URL') or os.environ.get('DATABASE_URL')
if not database_url:
    raise SystemExit('Configure DATABASE_URL or TEST_DATABASE_URL first.')
schema = 'cache_ui_' + uuid.uuid4().hex
server = None
try:
    with db.connect(database_url) as admin:
        admin.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
    dsn = make_conninfo(database_url, options='-csearch_path=' + schema)
    with db.connect(dsn) as conn:
        db.initialize(conn)
        candidate = db.development_profile(conn)['id']
        source = conn.execute("SELECT * FROM job_sources WHERE key='canadian-tech'").fetchone()
        snapshot = db.save_snapshot(conn, source['id'], 'Cache browser fixtures')
        ids = []
        for index in range(6):
            job = JobListing(company='Cache Fixture', title='Software Intern ' + str(index), location='Toronto',
                url='https://applications.example.test/' + str(index), source=source['repository'], source_id=source['id'],
                snapshot_id=str(snapshot['id']), source_line=index + 1, external_id='cache-' + str(index), status='open',
                posted_at=None if index == 1 else date(2026, 11, 3))
            db.save_job(conn, job)
            ids.append(db.get_source_job(conn, source['id'], job.external_id).canonical_job_id)
        fresh, unknown, saved, applied, dismissed, other = ids
        for job_id, event in ((saved, 'save'), (applied, 'applied'), (dismissed, 'dismiss')):
            db.record_interaction(conn, candidate, job_id, event)
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            port = listener.getsockname()[1]
        base = 'http://127.0.0.1:' + str(port)
        server = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'internshipfeed.api:app', '--host', '127.0.0.1', '--port', str(port)],
            cwd=ROOT, env=dict(os.environ, DATABASE_URL=dsn), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(100):
            try:
                with urlopen(base + '/api/health', timeout=1):
                    break
            except OSError:
                if server.poll() is not None:
                    raise RuntimeError('Temporary API exited during startup')
                time.sleep(0.1)
        else:
            raise RuntimeError('Temporary API did not become ready')
        with sync_playwright() as p:
            browser = p.chromium.launch()
            try:
                page = browser.new_page(viewport={'width': 1280, 'height': 1000}, timezone_id='America/Los_Angeles')
                owner=conn.execute('SELECT user_id FROM candidate_profiles WHERE id=%s',(candidate,)).fetchone()['user_id']
                page.context.add_cookies([{'name':'internshipfeed_session','value':str(owner),'url':base,'httpOnly':True,'sameSite':'Lax'}])
                screenshots = ROOT / 'data' / 'screenshots'
                screenshots.mkdir(parents=True, exist_ok=True)
                page.emulate_media(reduced_motion='reduce')
                counts, pending, modes, errors = Counter(), [], {}, []
                page.on('pageerror', lambda error: errors.append(str(error)))
                def intercept(route):
                    path = urlparse(route.request.url).path
                    if route.request.method != 'GET':
                        route.continue_()
                        return
                    counts[path] += 1
                    if modes.get(path) == 'hold':
                        pending.append((path, route))
                    elif modes.get(path) == 'fail':
                        route.fulfill(status=503, json={'detail': 'Temporary failure'})
                    else:
                        route.continue_()
                page.route('**/api/**', intercept)
                def release(path):
                    modes.pop(path, None)
                    for entry in list(pending):
                        if entry[0] == path:
                            pending.remove(entry)
                            entry[1].continue_()
                def card(job_id):
                    return page.locator('[data-job-id="%s"]' % job_id)
                def tab(name):
                    page.get_by_role('navigation').get_by_role('link', name=name, exact=True).click()

                modes['/api/recommendations'] = 'hold'
                page.goto(base, wait_until='domcontentloaded')
                expect(page.locator('.loading-state')).to_have_text('Loading jobs…')
                assert page.locator('.spinner').evaluate('(element) => getComputedStyle(element).width') == '18px'
                assert page.locator('.spinner').evaluate('(element) => getComputedStyle(element).animationName') == 'none'
                page.screenshot(path=str(screenshots / 'cache-initial.png'))
                release('/api/recommendations')
                expect(card(fresh)).to_be_visible()
                expect(card(fresh).locator('.posted-date')).to_have_text('Posted Nov 3, 2026')
                expect(card(unknown).locator('.posted-date')).to_have_count(0)
                page.screenshot(path=str(screenshots / 'cache-posted-date.png'))
                page.evaluate('window.cacheNavigationMarker = true')
                for label, job_id in (('All listings', fresh), ('Applied', applied), ('Saved', saved), ('Dismissed', dismissed), ('Recommended', fresh)):
                    tab(label)
                    expect(card(job_id)).to_be_visible()
                    expect(card(job_id).locator('.posted-date')).to_have_text('Posted Nov 3, 2026')
                initial_counts = counts.copy()
                for label in ('Applied', 'Saved', 'Dismissed', 'All listings', 'Recommended'):
                    tab(label)
                    expect(page.locator('.loading-state')).to_have_count(0)
                for path in ('/api/recommendations', '/api/feed', '/api/applied', '/api/saved', '/api/dismissed'):
                    assert counts[path] == initial_counts[path], (path, counts, initial_counts)
                assert page.evaluate('window.cacheNavigationMarker === true')
                page.go_back()
                expect(page.get_by_role('heading', name='All listings', exact=True)).to_be_visible()
                page.go_forward()
                expect(page.get_by_role('heading', name='Recommended', exact=True)).to_be_visible()
                print('PASS: initial spinner, five cached tabs, history navigation, and card dates')

                page.clock.install()
                tab('Saved')
                page.clock.fast_forward(46_000)
                modes['/api/recommendations'] = 'hold'
                tab('Recommended')
                expect(card(fresh)).to_be_visible()
                expect(page.locator('.loading-state')).to_have_count(0)
                expect(page.locator('.refresh-state')).to_have_text('Refreshing…')
                release('/api/recommendations')
                expect(page.locator('.refresh-state')).to_have_count(0)
                print('PASS: stale tab renders cached cards immediately while revalidating')

                modes['/api/profile'] = 'hold'
                page.get_by_role('button', name='Profile', exact=True).click()
                expect(page.locator('.loading-state')).to_have_text('Loading profile…')
                release('/api/profile')
                profile = page.get_by_role('dialog', name='Profile', exact=True)
                expect(profile.get_by_role('form', name='Candidate profile')).to_be_visible()
                profile.get_by_role('button', name='Close profile', exact=True).click()
                profile_requests = counts['/api/profile']
                page.get_by_role('button', name='Profile', exact=True).click()
                expect(profile.get_by_role('form', name='Candidate profile')).to_be_visible()
                assert counts['/api/profile'] == profile_requests
                profile.get_by_role('button', name='Close profile', exact=True).click()
                card(fresh).get_by_role('button', name='Software Intern 0', exact=True).click()
                details = page.get_by_role('dialog', name='Software Intern 0', exact=True)
                expect(details.locator('.posted-date')).to_have_text('Posted Nov 3, 2026')
                details.get_by_role('button', name='Close job details', exact=True).click()
                detail_requests = counts['/api/jobs/%s/details' % fresh]
                card(fresh).get_by_role('button', name='Software Intern 0', exact=True).click()
                expect(details.locator('.posted-date')).to_be_visible()
                assert counts['/api/jobs/%s/details' % fresh] == detail_requests
                details.get_by_role('button', name='Close job details', exact=True).click()
                card(unknown).get_by_role('button', name='Software Intern 1', exact=True).click()
                unknown_details = page.get_by_role('dialog', name='Software Intern 1', exact=True)
                expect(unknown_details.locator('.posted-date')).to_have_text('Posting date unavailable')
                unknown_details.get_by_role('button', name='Close job details', exact=True).click()
                print('PASS: cached profile/details and unknown-date presentation')

                tab('All listings')
                modes['/api/feed'] = 'hold'
                page.get_by_role('button', name='Refresh data', exact=True).click()
                expect(page.locator('.refresh-state')).to_have_text('Refreshing…')
                expect(card(fresh)).to_be_visible()
                expect(page.locator('.loading-state')).to_have_count(0)
                modes['/api/feed'] = 'fail'
                for entry in list(pending):
                    if entry[0] == '/api/feed':
                        pending.remove(entry)
                        entry[1].fulfill(status=503, json={'detail': 'Temporary failure'})
                expect(page.get_by_role('alert')).to_contain_text('Showing previously loaded data')
                expect(card(fresh)).to_be_visible()
                page.screenshot(path=str(screenshots / 'cache-refresh-error.png'))
                modes.pop('/api/feed')
                page.get_by_role('button', name='Try again', exact=True).click()
                expect(page.locator('.refresh-state')).to_have_count(0)
                print('PASS: background refresh/error retains content and retry recovers')

                tab('Recommended')
                modes['/api/recommendations'] = 'hold'
                card(fresh).get_by_role('button', name='Save job', exact=True).click()
                expect(card(fresh)).to_have_count(0)
                expect(card(unknown)).to_be_visible()
                expect(page.locator('.loading-state')).to_have_count(0)
                release('/api/recommendations')
                tab('Saved')
                expect(card(fresh)).to_be_visible()
                card(fresh).get_by_role('button', name='Unsave job', exact=True).click()
                expect(card(fresh)).to_have_count(0)
                tab('Recommended')
                expect(card(fresh)).to_be_visible()
                card(other).get_by_role('button', name='Dismiss job', exact=True).click()
                expect(card(other)).to_have_count(0)
                tab('Dismissed')
                expect(card(other)).to_be_visible()
                page.set_viewport_size({'width': 390, 'height': 844})
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                page.screenshot(path=str(screenshots / 'cache-mobile.png'))
                card(other).get_by_role('button', name='Restore job', exact=True).click()
                expect(card(other)).to_have_count(0)
                tab('Applied')
                card(applied).locator('.application-status select').select_option('interview')
                expect(card(applied).locator('.application-status select')).to_have_value('interview')
                tab('Recommended')
                expect(card(other)).to_be_visible()
                assert not errors, errors
                print('PASS: save/unsave, dismiss/restore and application status invalidate cached queries')
            finally:
                browser.close()
finally:
    if server is not None:
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=5)
    with db.connect(database_url) as admin:
        admin.execute(sql.SQL('DROP SCHEMA IF EXISTS {} CASCADE').format(sql.Identifier(schema)))
