"""Verify real paginated network reads, cached pages, filters, retries, and lazy details in an isolated schema."""
import os
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from urllib.request import urlopen

from dotenv import load_dotenv
from playwright.sync_api import expect, sync_playwright
from psycopg import sql
from psycopg.conninfo import make_conninfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from internshipfeed import db
from internshipfeed.models import JobKeywords, JobListing

load_dotenv(ROOT / '.env', override=False)
database_url = os.environ.get('TEST_DATABASE_URL')
if not database_url:
    raise SystemExit('Set TEST_DATABASE_URL to a disposable local database first.')
schema = 'pagination_ui_' + uuid.uuid4().hex
server = None
try:
    with db.connect(database_url) as conn:
        conn.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
    dsn = make_conninfo(database_url, options='-csearch_path=' + schema)
    with db.connect(dsn) as conn:
        db.initialize(conn)
        user, candidate = db.create_anonymous_candidate(conn)
        sources = conn.execute("SELECT id,repository FROM job_sources WHERE key IN ('canadian-tech','simplify') ORDER BY id").fetchall()
        snapshots = {source['id']: db.save_snapshot(conn, source['id'], 'Pagination browser fixtures') for source in sources}
        for index in range(175):
            source = sources[index % 2]
            db.save_job(conn, JobListing(company='NeedleCo' if index < 10 else 'CatalogCo',
                title=('Backend' if index % 2 else 'Frontend') + ' Intern ' + str(index),
                location='Toronto' if index % 3 else 'Vancouver', term='Summer 2027' if index % 2 else 'Fall 2027',
                url='https://applications.example.test/' + str(index), source=source['repository'], source_id=source['id'],
                external_id='pagination-' + str(index), snapshot_id=str(snapshots[source['id']]['id']), source_line=index + 1,
                status='open', description='Full description loaded separately. ' * 100,
                keywords=JobKeywords(languages=['Python'], requirements=['Detailed requirement. ' * 100])))
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
                    raise RuntimeError('Temporary pagination API exited')
                time.sleep(0.1)
        else:
            raise RuntimeError('Temporary pagination API did not start')
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            try:
                context = browser.new_context(viewport={'width': 1280, 'height': 1000})
                context.add_cookies([{'name': 'internshipfeed_session', 'value': str(user), 'url': base, 'httpOnly': True, 'sameSite': 'Lax'}])
                page = context.new_page()
                errors, requests, held = [], [], []
                mode = {'next': 'normal', 'first': 'normal'}
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.on('request', lambda request: requests.append(request.url) if request.method == 'GET' else None)

                def intercept(route):
                    next_page = 'cursor' in parse_qs(urlsplit(route.request.url).query)
                    current = mode['next' if next_page else 'first']
                    if current == 'hold':
                        held.append(route)
                    elif current == 'fail':
                        route.fulfill(status=503, json={'detail': 'Temporary page failure'})
                    else:
                        route.continue_()

                page.route(base + '/api/feed?*', intercept)
                rows = page.locator('.listing-row')

                def feed_requests():
                    return [url for url in requests if urlsplit(url).path == '/api/feed']

                def unique_rows():
                    ids = rows.evaluate_all('nodes => nodes.map(node => node.dataset.jobId)')
                    assert len(ids) == len(set(ids)), ids

                page.goto(base + '/listings')
                expect(rows).to_have_count(50)
                expect(page.locator('#summary')).to_have_text('50 of 175 jobs')
                assert len(feed_requests()) == 1
                assert not any('/details' in url for url in requests)
                measured = page.request.get(base + '/api/feed?limit=50')
                assert len(measured.body()) < 50000
                assert set(measured.json()['items'][0]) == {'job', 'term', 'skills', 'interaction_state'}

                mode['next'] = 'hold'
                page.get_by_role('button', name='Show more', exact=True).click()
                expect(page.get_by_role('status').filter(has_text='Loading more…')).to_be_visible()
                expect(rows).to_have_count(50)
                assert 'cursor' in parse_qs(urlsplit(held[0].request.url).query)
                mode['next'] = 'normal'
                held.pop().continue_()
                expect(rows).to_have_count(100)
                unique_rows()
                mode['next'] = 'fail'
                page.get_by_role('button', name='Show more', exact=True).click()
                expect(page.get_by_role('alert')).to_contain_text('Could not load more jobs.')
                expect(rows).to_have_count(100)
                mode['next'] = 'normal'
                page.get_by_role('button', name='Try again', exact=True).click()
                expect(rows).to_have_count(150)
                unique_rows()
                cached_requests = len(feed_requests())
                page.get_by_role('link', name='Recommended', exact=True).click()
                expect(page.locator('.recommendation-card')).to_have_count(30)
                page.get_by_role('button', name='Show more', exact=True).click()
                expect(page.locator('.recommendation-card')).to_have_count(60)
                ranked_requests = [url for url in requests if urlsplit(url).path == '/api/recommendations']
                assert parse_qs(urlsplit(ranked_requests[-1]).query)['offset'] == ['30']
                page.get_by_role('link', name='All listings', exact=True).click()
                expect(rows).to_have_count(150)
                expect(page.locator('.loading-state')).to_have_count(0)
                assert len(feed_requests()) == cached_requests

                mode['first'] = 'hold'
                page.get_by_role('button', name='Refresh data', exact=True).click()
                expect(page.locator('.refresh-state')).to_have_text('Refreshing…')
                expect(rows).to_have_count(150)
                mode['first'] = 'normal'
                held.pop().continue_()
                expect(page.locator('.refresh-state')).to_have_count(0)
                expect(rows).to_have_count(150)
                page.get_by_role('button', name='Show more', exact=True).click()
                expect(rows).to_have_count(175)
                expect(page.get_by_role('button', name='Show more', exact=True)).to_have_count(0)
                unique_rows()

                before_search = len(feed_requests())
                page.locator('#search').fill('Needle')
                page.locator('#search').fill('NeedleCo')
                expect(rows).to_have_count(10)
                expect(page.locator('#summary')).to_have_text('10 of 10 jobs')
                search_requests = feed_requests()[before_search:]
                assert len(search_requests) == 1, search_requests
                assert parse_qs(urlsplit(search_requests[0]).query)['q'] == ['needleco']
                assert all('cursor' not in parse_qs(urlsplit(url).query) for url in search_requests)
                page.locator('#search').fill('')
                expect(rows).to_have_count(175)
                page.select_option('#source', str(sources[0]['id']))
                expect(rows).to_have_count(50)
                expect(page.locator('#summary')).to_have_text('50 of 88 jobs')
                page.get_by_label('Location', exact=True).select_option('Toronto')
                expect(rows).to_have_count(50)
                expect(page.locator('#summary')).to_have_text('50 of 58 jobs')
                with page.expect_response(lambda response: urlsplit(response.url).path == '/api/feed' and parse_qs(urlsplit(response.url).query).get('term') == ['Fall 2027']):
                    page.get_by_label('Term', exact=True).select_option('Fall 2027')
                expect(page.locator('#summary')).to_have_text('50 of 58 jobs')
                latest = parse_qs(urlsplit(feed_requests()[-1]).query)
                assert latest['term'] == ['Fall 2027'] and latest['location'] == ['Toronto'] and 'cursor' not in latest
                page.get_by_role('link', name='Saved', exact=True).click()
                page.get_by_role('link', name='All listings', exact=True).click()
                expect(page.get_by_label('Location', exact=True)).to_have_value('Toronto')
                expect(page.get_by_label('Term', exact=True)).to_have_value('Fall 2027')
                expect(rows).to_have_count(50)

                requested_details = sum('/details' in url for url in requests)
                rows.first.locator('.job-title').click()
                expect(page.get_by_role('dialog').locator('.job-description')).to_contain_text('Full description loaded separately.')
                assert sum('/details' in url for url in requests) == requested_details + 1
                page.keyboard.press('Escape')
                expect(page.get_by_role('dialog')).to_have_count(0)
                page.set_viewport_size({'width': 320, 'height': 844})
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                assert not errors, errors
                print('Pagination browser checks passed: first 50; next 50 only; held/error/retry pages preserve content; 175 unique jobs; Recommended fetches 30 more; cached tab return; retained background refresh; debounced search outside loaded rows; source/location/term reset with independent caches; lazy full details; 320px layout. First-page fixture payload: %s bytes.' % len(measured.body()))
            finally:
                browser.close()
finally:
    if server is not None:
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait()
    with db.connect(database_url) as conn:
        conn.execute(sql.SQL('DROP SCHEMA IF EXISTS {} CASCADE').format(sql.Identifier(schema)))
