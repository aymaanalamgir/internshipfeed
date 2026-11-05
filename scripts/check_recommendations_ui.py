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
from internshipfeed.models import CandidateProfileUpdate, JobKeywords, JobListing, MatchResult

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
            owner=conn.execute('SELECT user_id FROM candidate_profiles WHERE id=%s',(candidate['id'],)).fetchone()['user_id']
            context.add_cookies([{'name':'internshipfeed_session','value':str(owner),'url':base,'httpOnly':True,'sameSite':'Lax'}])
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
            def mark_applied(job_id):
                card(job_id).locator('.job-title').click()
                drawer = page.get_by_role('dialog')
                drawer.get_by_role('button', name='Mark job as applied', exact=True).click()
                expect(drawer.get_by_role('button', name='Unmark job as applied', exact=True)).to_be_visible()
                page.keyboard.press('Escape')
            page.goto(base + '/recommendations')
            expect(page.locator('.recommendation-card')).to_have_count(4)
            assert card(first).locator('.recommendation-actions').locator('a,button').all_text_contents() == ['Apply', 'Save', '']
            expect(card(first).locator('.match-hint')).to_contain_text('Engineering')
            explain = card(first).get_by_role('button', name='Why this job? ' + original[0]['job']['title'], exact=True)
            expect(explain).to_have_attribute('aria-expanded', 'false')
            explain.click()
            expect(explain).to_have_attribute('aria-expanded', 'true')
            expect(card(first).locator('.match-explanation')).to_contain_text('Matches your target role')
            expect(card(first).locator('.match-explanation')).to_contain_text('Matches your skills')
            expect(card(first).locator('.match-explanation')).to_contain_text('Fits Summer 2027')
            expect(card(first).locator('.behavior-reasons')).to_have_count(0)
            assert not any(value in card(first).inner_text().lower() for value in ('profile fit', 'preference fit', '75%', '25%', 'starts at 50', ' / 40', ' / 30', ' / 20', ' / 10'))
            explain.click()
            with browser.new_context() as fresh_context:
                fresh = fresh_context.new_page()
                fresh.goto(base + '/recommendations')
                expect(fresh.locator('.match-hint').first).to_have_text('Personalization is still getting started.')
                fresh.locator('.match-toggle').first.click()
                expect(fresh.locator('.match-explanation')).to_contain_text('Add your target roles, skills, locations, and internship terms')
                fresh.locator('.match-explanation').get_by_role('button', name='Edit profile', exact=True).click()
                expect(fresh.get_by_role('dialog', name='Profile', exact=True)).to_be_visible()
            print('Explanation checks passed: positive evidence, collapsed/expanded accessibility, hidden ranking math, and a fresh browser with working Edit profile guidance.')
            screenshots = ROOT / 'data' / 'screenshots'
            screenshots.mkdir(parents=True, exist_ok=True)
            page.evaluate('document.fonts.ready')
            page.screenshot(path=str(screenshots / 'recommended-desktop.png'), full_page=True)
            page.set_viewport_size({'width': 390, 'height': 844})
            page.evaluate('new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))')
            page.screenshot(path=str(screenshots / 'recommended-mobile.png'), full_page=True)
            page.set_viewport_size({'width': 1280, 'height': 1000})
            card(first).locator('.job-title').click()
            detail = page.get_by_role('dialog')
            expect(detail.locator('.job-description')).to_have_text('Build software tools.')
            expect(detail.locator('.skill-tag').first).to_be_visible()
            assert page.url == base + '/recommendations'

            held = []
            path = '**/api/jobs/%s/interactions' % first
            page.route(path, lambda route: held.append(route))
            detail.get_by_role('button', name='Save job', exact=True).click()
            expect(card(first).get_by_role('button', name='Save job', exact=True)).to_have_text('Saving…')
            expect(card(first).get_by_role('button', name='Save job', exact=True)).to_have_attribute('aria-pressed', 'false')
            assert count('save') == 0
            held[0].continue_()
            page.unroute(path)
            expect(card(first)).to_have_count(0)
            expect(detail.get_by_role('button', name='Unsave job', exact=True)).to_have_attribute('aria-pressed', 'true')
            assert count('save') == 1
            page.keyboard.press('Escape')
            expect(page.get_by_role('heading', name='Recommended', exact=True)).to_be_focused()
            page.reload()
            expect(card(first)).to_have_count(0)
            page.get_by_role('link', name='Saved', exact=True).click()
            expect(card(first).get_by_role('button', name='Unsave job', exact=True)).to_be_visible()
            page.reload()
            expect(card(first)).to_be_visible()
            page.route(path, lambda route: route.fulfill(status=503, json={'detail': 'private diagnostics'}))
            with page.expect_popup() as popup:
                card(first).get_by_role('link', name='Apply', exact=True).click()
            popup.value.wait_for_load_state()
            assert popup.value.url == original[0]['job']['application_url']
            assert popup.value.evaluate('window.opener === null')
            popup.value.close()
            expect(card(first).get_by_text('Click could not be recorded. The application link is still available.', exact=True)).to_be_visible()
            assert count('click') == 0
            page.get_by_role('button', name='Keep in feed', exact=True).click()
            page.unroute(path)
            with page.expect_response(lambda response: response.url.endswith('/interactions') and response.request.method == 'POST') as click_response:
                with page.expect_popup() as popup:
                    card(first).get_by_role('link', name='Apply', exact=True).click()
            assert click_response.value.status == 201
            popup.value.wait_for_load_state()
            assert popup.value.evaluate('window.opener === null')
            popup.value.close()
            assert count('click') == 1
            page.get_by_role('button', name='Keep in feed', exact=True).click()
            page.get_by_role('link', name='Recommended', exact=True).click()
            expect(card(first)).to_have_count(0)

            dismiss_path = '**/api/jobs/%s/interactions' % second
            page.route(dismiss_path, lambda route: route.fulfill(status=503, json={'detail': 'private diagnostics'}))
            card(second).get_by_role('button', name='Dismiss job', exact=True).click()
            expect(card(second).get_by_text('Could not dismiss this job. Please try again.', exact=True)).to_be_visible()
            assert count('dismiss') == 0
            page.unroute(dismiss_path)
            card(second).get_by_role('button', name='Dismiss job', exact=True).click()
            expect(card(second)).to_have_count(0)
            assert count('dismiss') == 1
            page.reload()
            expect(card(first)).to_have_count(0)
            expect(card(second)).to_have_count(0)

            mark_applied(third)
            expect(card(third)).to_have_count(0)
            assert count('applied') == 1
            page.reload()
            expect(card(first)).to_have_count(0)
            expect(card(third)).to_have_count(0)
            page.get_by_role('link', name='Applied', exact=True).click()
            expect(card(third).get_by_role('button', name='Unmark job as applied', exact=True)).to_have_text('Applied')
            expect(card(third).locator('.applied-date')).to_be_visible()
            expect(card(third).get_by_role('link', name='Open application', exact=True)).to_be_visible()
            expect(card(third).get_by_role('button', name='Dismiss job', exact=True)).to_have_count(0)
            card(third).get_by_role('button', name='Save job', exact=True).click()
            expect(card(third).get_by_role('button', name='Unsave job', exact=True)).to_be_visible()
            page.reload()
            expect(card(third).get_by_role('button', name='Unmark job as applied', exact=True)).to_be_visible()
            expect(card(third).get_by_role('button', name='Unsave job', exact=True)).to_be_visible()
            page.get_by_role('link', name='Saved', exact=True).click()
            expect(card(first)).to_be_visible()
            expect(card(third).get_by_role('button', name='Unmark job as applied', exact=True)).to_be_visible()
            page.reload()
            expect(card(third).get_by_role('button', name='Unsave job', exact=True)).to_be_visible()
            assert {row['job']['id'] for row in page.request.get(base + '/api/recommendations').json()} == {ids[3]}
            for endpoint in ('recommendations', 'saved', 'applied'):
                for row in page.request.get(base + '/api/' + endpoint).json():
                    old = next(item for item in original if item['job']['id'] == row['job']['id'])
                    assert {key: row[key] for key in MatchResult.model_fields} == {key: old[key] for key in MatchResult.model_fields}
            assert conn.execute('SELECT * FROM canonical_jobs ORDER BY id').fetchall() == before
            events = conn.execute('SELECT * FROM job_interactions ORDER BY id').fetchall()
            assert [event['interaction_type'] for event in events] == ['save', 'click', 'dismiss', 'applied', 'save']
            assert all(event['candidate_profile_id'] == candidate['id'] for event in events)

            card(third).get_by_role('button', name='Why this job? ' + original[2]['job']['title'], exact=True).click()
            expect(card(third).locator('.match-explanation')).to_contain_text('Matches your target role')
            expect(card(third).locator('.score-breakdown')).to_have_count(0)
            page.evaluate('document.fonts.ready')
            screenshots = ROOT / 'data' / 'screenshots'
            screenshots.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(screenshots / 'saved-desktop.png'), full_page=True)
            page.set_viewport_size({'width': 390, 'height': 844})
            page.evaluate('new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))')
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            page.screenshot(path=str(screenshots / 'saved-mobile.png'), full_page=True)
            page.set_viewport_size({'width': 320, 'height': 844})
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            page.route('**/api/saved', lambda route: route.fulfill(status=503, json={'detail': 'private diagnostics'}))
            page.reload()
            expect(page.get_by_text('Could not load saved jobs.', exact=True)).to_be_visible()
            expect(page.locator('.recommendation-card')).to_have_count(0)
            page.unroute('**/api/saved')
            page.get_by_role('button', name='Try again', exact=True).click()
            expect(card(first)).to_be_visible()
            card(first).get_by_role('button', name='Dismiss job', exact=True).click()
            expect(card(first)).to_have_count(0)
            page.reload()
            expect(card(third)).to_be_visible()
            expect(card(first)).to_have_count(0)
            page.get_by_role('link', name='Applied', exact=True).click()
            expect(card(third)).to_be_visible()
            page.set_viewport_size({'width': 1280, 'height': 1000})
            page.evaluate('new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))')
            page.screenshot(path=str(screenshots / 'applied-desktop.png'), full_page=True)
            page.set_viewport_size({'width': 390, 'height': 844})
            page.evaluate('new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))')
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            page.screenshot(path=str(screenshots / 'applied-mobile.png'), full_page=True)
            page.get_by_role('link', name='Recommended', exact=True).click()
            expect(card(ids[3])).to_be_visible()
            for hidden in (first, second, third):
                expect(card(hidden)).to_have_count(0)
            page.reload()
            expect(card(ids[3])).to_be_visible()
            # Known ranking example, using new role/company metadata unrelated to
            # the earlier workflow fixtures. All three have identical profile fit.
            rank_ids = []
            for index, title in enumerate(('Data Engineer Intern', 'Data Engineer Intern', 'Product Manager Intern')):
                job = JobListing(company='Ranking Lab', title=title, location='Unknown',
                    url='https://applications.example.test/rank/' + str(index), source=source['repository'], source_id=source['id'],
                    external_id='rank-' + str(index), status='open', snapshot_id=str(snapshot['id']), source_line=index + 10)
                db.save_job(conn, job)
                rank_ids.append(db.get_source_job(conn, source['id'], job.external_id).canonical_job_id)
            history_id, data_id, product_id = rank_ids
            update = page.request.put(base + '/api/profile', data={'target_roles': ['Data Engineering', 'Product Management'],
                'skills': [], 'preferred_locations': [], 'preferred_terms': [], 'graduation_year': None})
            assert update.status == 200
            baseline = [row for row in page.request.get(base + '/api/recommendations').json() if row['job']['id'] in rank_ids]
            assert [row['job']['id'] for row in baseline] == [product_id, data_id, history_id]
            assert all((row['profile_score'], row['behavior_score'], row['final_score']) == (40, 50, 43) for row in baseline)
            page.set_viewport_size({'width': 1280, 'height': 1000})
            page.goto(base + '/recommendations')
            card(history_id).get_by_role('button', name='Save job', exact=True).click()
            expect(card(history_id)).to_have_count(0)
            page.get_by_role('link', name='Saved', exact=True).click()
            mark_applied(history_id)
            page.get_by_role('link', name='Recommended', exact=True).click()
            expect(page.get_by_role('heading', name='Recommended', exact=True)).to_be_visible()
            expect(card(data_id)).to_be_visible()
            expect(card(history_id)).to_have_count(0)
            page.reload()
            expect(card(data_id)).to_be_visible()
            personalized = [row for row in page.request.get(base + '/api/recommendations').json() if row['job']['id'] in rank_ids]
            assert [row['job']['id'] for row in personalized] == [data_id, product_id]
            assert [(row['profile_score'], row['behavior_score'], row['final_score']) for row in personalized] == [(40, 65, 46), (40, 52, 43)]
            assert page.locator('.recommendation-card').first.get_attribute('data-job-id') == str(data_id)
            expect(card(data_id).locator('.match-score')).to_contain_text('46')
            card(data_id).get_by_role('button', name='Why this job? Data Engineer Intern', exact=True).click()
            expect(card(data_id).locator('.match-reasons')).to_contain_text('Matches your target role: Data Engineering')
            expect(card(data_id).locator('.ranking-breakdown')).to_have_count(0)
            expect(card(data_id).locator('.behavior-reasons')).to_contain_text("Similar to roles you've shown interest in")
            weak = card(ids[3])
            weak.locator('.match-toggle').click()
            expect(weak.locator('.match-explanation')).to_contain_text('Limited match')
            expect(weak.locator('.match-explanation')).to_contain_text('Role is outside your main targets')
            page.evaluate('document.fonts.ready')
            page.screenshot(path=str(screenshots / 'behavioral-recommended-desktop.png'), full_page=True)
            page.set_viewport_size({'width': 390, 'height': 844})
            page.evaluate('new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))')
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            page.screenshot(path=str(screenshots / 'behavioral-recommended-mobile.png'), full_page=True)
            page.reload()
            expect(card(data_id)).to_be_visible()
            assert page.locator('.recommendation-card').first.get_attribute('data-job-id') == str(data_id)
            print('Known ranking verified after refresh: Data/Product profile fit 40/40; neutral rank 43/43; save+apply Data history changes behavior to 65/52 and final rank to 46/43, placing Data first.')
            # Undo from history views, then toggle both controls inside details.
            page.get_by_role('link', name='Saved', exact=True).click()
            expect(card(history_id)).to_be_visible()
            inverse_path = '**/api/jobs/%s/interactions' % history_id
            page.route(inverse_path, lambda route: route.fulfill(status=503, json={'detail': 'private diagnostics'}))
            card(history_id).get_by_role('button', name='Unsave job', exact=True).click()
            expect(card(history_id).get_by_text('Could not unsave this job. Please try again.', exact=True)).to_be_visible()
            page.unroute(inverse_path)
            pending_inverse = []
            page.route(inverse_path, lambda route: pending_inverse.append(route))
            card(history_id).get_by_role('button', name='Unsave job', exact=True).click()
            expect(card(history_id).get_by_role('button', name='Unsave job', exact=True)).to_have_text('Removing…')
            expect(card(history_id)).to_be_visible()
            pending_inverse[0].continue_()
            page.unroute(inverse_path)
            expect(card(history_id)).to_have_count(0)
            page.reload()
            expect(card(history_id)).to_have_count(0)
            page.get_by_role('link', name='Applied', exact=True).click()
            expect(card(history_id).get_by_role('button', name='Save job', exact=True)).to_be_visible()
            page.route(inverse_path, lambda route: route.fulfill(status=503, json={'detail': 'private diagnostics'}))
            card(history_id).get_by_role('button', name='Unmark job as applied', exact=True).click()
            expect(card(history_id).get_by_text('Could not unmark this job as applied. Please try again.', exact=True)).to_be_visible()
            page.unroute(inverse_path)
            card(history_id).get_by_role('button', name='Unmark job as applied', exact=True).click()
            expect(card(history_id)).to_have_count(0)
            page.reload()
            expect(card(history_id)).to_have_count(0)
            page.get_by_role('link', name='Recommended', exact=True).click()
            expect(card(history_id)).to_be_visible()
            card(history_id).locator('.job-title').click()
            undo_detail = page.get_by_role('dialog')
            undo_detail.get_by_role('button', name='Save job', exact=True).click()
            expect(card(history_id)).to_have_count(0)
            expect(undo_detail.get_by_role('button', name='Unsave job', exact=True)).to_have_attribute('aria-pressed', 'true')
            undo_detail.get_by_role('button', name='Unsave job', exact=True).click()
            expect(card(history_id).get_by_role('button', name='Save job', exact=True)).to_have_attribute('aria-pressed', 'false')
            undo_detail.get_by_role('button', name='Mark job as applied', exact=True).click()
            expect(card(history_id)).to_have_count(0)
            undo_detail.get_by_role('button', name='Unmark job as applied', exact=True).click()
            expect(card(history_id)).to_be_visible()
            page.keyboard.press('Escape')
            page.reload()
            expect(card(history_id)).to_be_visible()
            expect(card(history_id).get_by_role('button', name='Save job', exact=True)).to_be_visible()
            expect(card(history_id).get_by_role('button', name='Mark job as applied', exact=True)).to_have_count(0)
            undo_history = conn.execute('SELECT interaction_type FROM job_interactions WHERE canonical_job_id=%s ORDER BY id', (history_id,)).fetchall()
            assert [row['interaction_type'] for row in undo_history] == ['save', 'applied', 'unsave', 'unapplied', 'save', 'unsave', 'applied', 'unapplied']
            assert history_id not in [entry.details.job.id for entry in db.behavior_history(conn, candidate['id'])]
            print('Reversible controls verified: Save/Unsave and Applied/Unmark, shared drawer state, pending confirmation, failure/retry, Saved/Applied removal, restored Recommended membership, refresh persistence, and canceled preference evidence.')
            # Dismissed is a persistent collection with explicit, confirmed Restore.
            card(history_id).locator('.job-title').click()
            page.get_by_role('dialog').get_by_role('button', name='Dismiss job', exact=True).click()
            expect(page.get_by_role('dialog')).to_have_count(0)
            expect(card(history_id)).to_have_count(0)
            page.get_by_role('link', name='Dismissed', exact=True).click()
            expect(page.get_by_role('heading', name='Dismissed', exact=True)).to_be_visible()
            expect(card(history_id).get_by_role('button', name='Restore job', exact=True)).to_be_visible()
            expect(card(history_id).locator('.applied-date')).to_contain_text('Dismissed')
            page.reload()
            expect(card(history_id)).to_be_visible()
            page.set_viewport_size({'width': 320, 'height': 844})
            page.get_by_role('link', name='Dismissed', exact=True).scroll_into_view_if_needed()
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            page.screenshot(path=str(screenshots / 'dismissed-mobile.png'), full_page=True)
            page.set_viewport_size({'width': 1280, 'height': 1000})
            page.screenshot(path=str(screenshots / 'dismissed-desktop.png'), full_page=True)
            page.route('**/api/dismissed', lambda route: route.fulfill(status=503, json={'detail': 'private diagnostics'}))
            page.reload()
            expect(page.get_by_text('Could not load dismissed jobs.', exact=True)).to_be_visible()
            page.unroute('**/api/dismissed')
            page.get_by_role('button', name='Try again', exact=True).click()
            expect(card(history_id)).to_be_visible()
            page.route(inverse_path, lambda route: route.fulfill(status=503, json={'detail': 'private diagnostics'}))
            card(history_id).get_by_role('button', name='Restore job', exact=True).click()
            expect(card(history_id).get_by_text('Could not restore this job. Please try again.', exact=True)).to_be_visible()
            page.unroute(inverse_path)
            held_restore = []
            page.route(inverse_path, lambda route: held_restore.append(route))
            card(history_id).get_by_role('button', name='Restore job', exact=True).click()
            expect(card(history_id).get_by_role('button', name='Restore job', exact=True)).to_have_text('Restoring…')
            expect(card(history_id)).to_be_visible()
            held_restore[0].continue_()
            page.unroute(inverse_path)
            expect(card(history_id)).to_have_count(0)
            page.reload()
            expect(card(history_id)).to_have_count(0)
            page.get_by_role('link', name='Recommended', exact=True).click()
            expect(card(history_id)).to_be_visible()
            card(history_id).get_by_role('button', name='Dismiss job', exact=True).click()
            expect(card(history_id)).to_have_count(0)
            page.get_by_role('link', name='Dismissed', exact=True).click()
            card(history_id).locator('.job-title').click()
            page.get_by_role('dialog').get_by_role('button', name='Restore job', exact=True).click()
            expect(page.get_by_role('dialog')).to_have_count(0)
            expect(card(history_id)).to_have_count(0)
            assert history_id not in [entry.details.job.id for entry in db.behavior_history(conn, candidate['id'])]
            page.get_by_role('link', name='Recommended', exact=True).click()
            expect(card(history_id)).to_be_visible()
            print('Dismissed browser checks passed: navigation, newest history, mobile width, persistent state, load/retry, restore failure/pending/confirmation, card and drawer restoration, and canceled negative ranking evidence.')
            assert not errors, errors
            browser.close()
            print('Product-state browser + PostgreSQL checks passed: Recommended/Saved/Applied membership, Save+Applied and Save+Dismiss precedence, navigation, repeated refreshes, applied timestamp, click/new-tab safety, failed-click navigation, failure/retry, unchanged scores, and mobile widths.')

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
