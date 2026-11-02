"""Fixture ingestion, CLI dry run, and browser email preview in an isolated schema."""
import argparse
import io
import os
import sys
import tempfile
import uuid
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from playwright.sync_api import expect, sync_playwright
from psycopg import sql
from psycopg.conninfo import make_conninfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from internshipfeed import db
from internshipfeed.daily_refresh import DailySettings, main, run_daily_refresh
from internshipfeed.models import CandidateProfileUpdate, JobKeywords

parser = argparse.ArgumentParser()
parser.add_argument('--browser-executable')
args = parser.parse_args()
with db.connect() as admin:
    schema = 'email_preview_' + uuid.uuid4().hex
    admin.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
    dsn = make_conninfo(admin.info.dsn, options='-csearch_path=' + schema)
    original_connect = db.connect
    isolated = lambda: original_connect(dsn)
    try:
        with isolated() as conn:
            db.initialize(conn)
            db.update_development_profile(conn, CandidateProfileUpdate(target_roles=['Software Engineering'], skills=['Python', 'SQL'],
                preferred_locations=['Toronto'], preferred_terms=['Winter 2027'], graduation_year=2027))
            with tempfile.TemporaryDirectory() as directory, patch('internshipfeed.db.connect', side_effect=isolated), \
                 patch('internshipfeed.daily_refresh.send_email') as provider:
                result = run_daily_refresh(DailySettings(0), dry_run=True, ingestion_options={'enrich': False, 'output': directory,
                    'input_paths': {'canadian-tech': ROOT / 'tests/fixtures/listings.md', 'simplify': ROOT / 'tests/fixtures/simplify.md'}})
                assert result['status'] == 'dry_run' and result['new_jobs'] > 0
                # Attach controlled job keywords to show the digest's matched-skill tags.
                conn.execute("UPDATE source_postings SET keywords=%s", (db.Jsonb(JobKeywords(languages=['Python', 'SQL']).model_dump()),))
                from internshipfeed.reconciliation import refresh_canonical
                for row in conn.execute('SELECT id FROM canonical_jobs').fetchall():
                    refresh_canonical(conn, row['id'])
                result = run_daily_refresh(DailySettings(0), dry_run=True, batch_id=result['batch_id'])
                with patch.dict(os.environ, {'DAILY_EMAIL_MIN_SCORE': '0', 'DAILY_NOTIFICATION_CHANNEL': 'email'}), redirect_stdout(io.StringIO()) as output:
                    assert main(['--dry-run', '--no-ingest', '--batch-id', result['batch_id']]) == 0
                assert result['subject'] in output.getvalue() and result['html'] in output.getvalue()
                provider.assert_not_called()
                assert conn.execute('SELECT count(*) AS n FROM job_notifications').fetchone()['n'] == 0
                from internshipfeed.api import app
                from fastapi.testclient import TestClient
                with TestClient(app) as client:
                    expected = client.get('/api/recommendations').json()
                positions = [result['html'].index(row['job']['application_url']) for row in expected if row['job']['application_url'] and row['job']['application_url'] in result['html']]
                assert positions == sorted(positions)
            artifacts = ROOT / 'data/screenshots'
            artifacts.mkdir(parents=True, exist_ok=True)
            (artifacts / 'daily-email-preview.html').write_text(result['html'])
            (artifacts / 'daily-email-preview.txt').write_text(result['subject'] + '\n\n' + result['message'])
            with sync_playwright() as p:
                browser = p.chromium.launch(executable_path=args.browser_executable, headless=True)
                try:
                    page = browser.new_page(viewport={'width': 800, 'height': 1000})
                    page.set_content(result['html'])
                    expect(page.get_by_role('heading', level=1)).to_contain_text('matched your profile')
                    assert page.get_by_role('link', name='Apply', exact=False).count() > 0
                    assert page.get_by_text('Python', exact=True).count() > 0
                    page.screenshot(path=str(artifacts / 'daily-email-desktop.png'), full_page=True)
                    page.set_viewport_size({'width': 390, 'height': 844})
                    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
                    page.screenshot(path=str(artifacts / 'daily-email-mobile.png'), full_page=True)
                finally:
                    browser.close()
            print('Daily email verified: real two-source fixture ingestion, CLI dry-run output, API rank ordering, rendered content/mobile, no provider call, no notification rows.')
    finally:
        admin.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))
