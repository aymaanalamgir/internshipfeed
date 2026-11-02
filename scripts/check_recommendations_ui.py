"""Real browser/API/PostgreSQL workflow in an isolated, disposable schema."""
import argparse
import os
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path
from urllib.request import urlopen

import psycopg
from dotenv import load_dotenv
from playwright.sync_api import expect, sync_playwright
from psycopg import sql
from psycopg.conninfo import make_conninfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from internshipfeed import db
from internshipfeed.models import CandidateProfileUpdate, JobKeywords, JobListing

parser = argparse.ArgumentParser()
parser.add_argument('--browser-executable')
args = parser.parse_args()
load_dotenv(ROOT / '.env', override=False)
database_url = os.environ.get('TEST_DATABASE_URL') or os.environ.get('DATABASE_URL')
if not database_url:
    raise SystemExit('Configure DATABASE_URL or TEST_DATABASE_URL first.')
schema = 'ui_workflow_' + uuid.uuid4().hex
dsn = make_conninfo(database_url, options='-csearch_path=' + schema)
server = None
try:
    with db.connect(database_url) as admin:
        admin.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
    with db.connect(dsn) as conn:
        db.initialize(conn)
        candidate = db.development_profile(conn)
        db.update_development_profile(conn, CandidateProfileUpdate(
            target_roles=['Software Engineering', 'Backend Engineering'], skills=['Python', 'SQL', 'Docker'],
            preferred_locations=['Toronto', 'Remote Canada'], preferred_terms=['Summer 2027'], graduation_year=2027))
        source = conn.execute("SELECT id, repository FROM job_sources WHERE key='canadian-tech'").fetchone()
        snapshot = db.save_snapshot(conn, source['id'], 'Deterministic recommendation UI fixtures')
        jobs = [
            ('Northstar Labs', 'Backend Software Engineer Intern', 'Toronto, ON', JobKeywords(languages=['Python', 'SQL'], tools_and_platforms=['Docker'])),
            ('Fieldwork', 'Software Engineering Intern', 'Remote, Canada', JobKeywords(languages=['Python', 'SQL'], tools_and_platforms=['Docker', 'AWS'])),
            ('Maple Research', 'Software Developer Intern', 'Ottawa, ON', JobKeywords(languages=['Python', 'SQL'], tools_and_platforms=['Docker'])),
            ('Quiet Systems', 'Application Developer Co-op', 'Vancouver, BC', JobKeywords(languages=['Python'], tools_and_platforms=['Docker', 'Kubernetes'])),
        ]
        for index, (company, title, location, keywords) in enumerate(jobs, 1):
            db.save_job(conn, JobListing(company=company, title=title, location=location,
                url='https://applications.example.test/jobs/' + str(index), source=source['repository'], source_id=source['id'],
                external_id='ui-' + str(index), status='open', snapshot_id=str(snapshot['id']), source_line=index,
                term='Summer 2027', keywords=keywords, enrichment_status='success', description='Build software tools.'))
        before = conn.execute('SELECT * FROM canonical_jobs ORDER BY id').fetchall()

        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            port = listener.getsockname()[1]
        base = 'http://127.0.0.1:' + str(port)
        environment = dict(os.environ, DATABASE_URL=dsn)
        server = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'internshipfeed.api:app', '--host', '127.0.0.1', '--port', str(port)],
                                  cwd=ROOT, env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(100):
            try:
                with urlopen(base + '/api/health', timeout=1) as response:
                    if response.status == 200:
                        break
            except OSError:
                if server.poll() is not None:
                    raise RuntimeError('Isolated API server exited during startup')
                time.sleep(0.1)
        else:
            raise RuntimeError('Isolated API server did not become ready')

        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=args.browser_executable, headless=True)
            context = browser.new_context(viewport={'width': 1280, 'height': 1000})
            # Exercise real external-tab navigation, with a local response at the
            # reserved fixture URL rather than contacting any employer's website.
            def external(route):
                assert 'referer' not in route.request.headers
                route.fulfill(body='<html><title>Application fixture</title><p>Application</p></html>', content_type='text/html')
            context.route('https://applications.example.test/**', external)
            page = context.new_page()
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            original = page.request.get(base + '/api/recommendations').json()
            ids = [row['job']['id'] for row in original]
            first, second, third = ids[:3]
            def card(job_id):
                return page.locator('[data-job-id="%s"]' % job_id)
            def count(action):
                return conn.execute('SELECT count(*) AS n FROM job_interactions WHERE interaction_type=%s', (action,)).fetchone()['n']
            page.goto(base + '/recommendations')
            expect(page.locator('.recommendation-card')).to_have_count(4)
            assert card(first).locator('.recommendation-actions').locator('a,button').all_text_contents() == ['Apply', 'Save', 'Mark applied', '']

            held = []
            path = '**/api/jobs/%s/interactions' % first
            page.route(path, lambda route: held.append(route))
            card(first).get_by_role('button', name='Save job', exact=True).click()
            expect(card(first).get_by_role('button', name='Save job', exact=True)).to_have_text('Saving…')
            expect(card(first).get_by_role('button', name='Save job', exact=True)).to_have_attribute('aria-pressed', 'false')
            assert count('save') == 0
            held[0].continue_()
            page.unroute(path)
            expect(card(first).get_by_role('button', name='Job saved', exact=True)).to_have_attribute('aria-pressed', 'true')
            assert count('save') == 1

            page.route(path, lambda route: route.fulfill(status=503, json={'detail': 'private diagnostics'}))
            with page.expect_popup() as popup:
                card(first).get_by_role('link', name='Apply', exact=True).click()
            popup.value.wait_for_load_state()
            assert popup.value.url == original[0]['job']['application_url']
            assert popup.value.evaluate('window.opener === null')
            popup.value.close()
            expect(card(first).get_by_text('Click could not be recorded. The application link is still available.', exact=True)).to_be_visible()
            assert count('click') == 0
            page.unroute(path)
            with page.expect_response(lambda response: response.url.endswith('/interactions') and response.request.method == 'POST') as click_response:
                with page.expect_popup() as popup:
                    card(first).get_by_role('link', name='Apply', exact=True).click()
            assert click_response.value.status == 201
            popup.value.wait_for_load_state()
            assert popup.value.evaluate('window.opener === null')
            popup.value.close()
            assert count('click') == 1
            expect(card(first).get_by_role('button', name='Mark job as applied', exact=True)).to_have_attribute('aria-pressed', 'false')

            page.route(path, lambda route: route.fulfill(status=503, json={'detail': 'private diagnostics'}))
            card(first).get_by_role('button', name='Dismiss job', exact=True).click()
            expect(card(first).get_by_text('Could not dismiss this job. Please try again.', exact=True)).to_be_visible()
            assert count('dismiss') == 0
            page.unroute(path)
            card(first).get_by_role('button', name='Dismiss job', exact=True).click()
            expect(card(first)).to_have_count(0)
            assert count('dismiss') == 1

            card(second).get_by_role('button', name='Mark job as applied', exact=True).click()
            expect(card(second).get_by_role('button', name='Job marked as applied', exact=True)).to_have_text('Applied')
            assert count('applied') == 1
            card(third).get_by_role('button', name='Save job', exact=True).click()
            expect(card(third).get_by_role('button', name='Job saved', exact=True)).to_be_visible()
            page.reload()
            expect(card(first)).to_have_count(0)
            expect(card(second).get_by_role('button', name='Job marked as applied', exact=True)).to_be_visible()
            expect(card(third).get_by_role('button', name='Job saved', exact=True)).to_be_visible()
            assert page.request.get(base + '/api/recommendations').json() == original
            assert conn.execute('SELECT * FROM canonical_jobs ORDER BY id').fetchall() == before
            events = conn.execute('SELECT * FROM job_interactions ORDER BY id').fetchall()
            assert [event['interaction_type'] for event in events] == ['save', 'click', 'dismiss', 'applied', 'save']
            assert all(event['candidate_profile_id'] == candidate['id'] for event in events)

            card(second).get_by_role('button', name='Why this score for ' + original[1]['job']['title'], exact=True).click()
            expect(card(second).locator('.score-breakdown')).to_be_visible()
            page.evaluate('document.fonts.ready')
            screenshots = ROOT / 'data' / 'screenshots'
            screenshots.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(screenshots / 'recommendations-desktop.png'), full_page=True)
            page.set_viewport_size({'width': 390, 'height': 844})
            page.evaluate('new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))')
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            page.screenshot(path=str(screenshots / 'recommendations-mobile.png'), full_page=True)
            page.set_viewport_size({'width': 320, 'height': 844})
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            page.route('**/api/interactions', lambda route: route.fulfill(status=503, json={'detail': 'private diagnostics'}))
            page.reload()
            expect(page.get_by_text('Could not load recommendations and job states.', exact=True)).to_be_visible()
            expect(page.locator('.recommendation-card')).to_have_count(0)
            page.unroute('**/api/interactions')
            page.get_by_role('button', name='Try again', exact=True).click()
            expect(card(second)).to_be_visible()
            assert not errors, errors
            browser.close()
            print('Recommendation browser + PostgreSQL checks passed: confirmed Save, Dismiss, Applied, click/new-tab safety, failed-click navigation, failure/retry, refresh persistence, isolated candidate events, unchanged scores, and mobile widths.')

        for script in ('check_profile_ui.py', 'check_ui.py'):
            command = [sys.executable, str(ROOT / 'scripts' / script), '--url', base]
            if args.browser_executable:
                command += ['--browser-executable', args.browser_executable]
            subprocess.run(command, cwd=ROOT, check=True)
finally:
    if server is not None:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait()
    with db.connect(database_url) as admin:
        admin.execute(sql.SQL('DROP SCHEMA IF EXISTS {} CASCADE').format(sql.Identifier(schema)))
    print('Isolated workflow schema and temporary API server removed; live candidate history was not modified.')
