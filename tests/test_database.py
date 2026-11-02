"""Real PostgreSQL tests; run with scripts/test_with_postgres.py or TEST_DATABASE_URL."""
import json
import os
import tempfile
import time
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

import psycopg
from fastapi.testclient import TestClient
from psycopg import sql
from psycopg.conninfo import make_conninfo

from internshipfeed import db
from internshipfeed.api import app, connection
from internshipfeed.cli import main
from internshipfeed.sources import CANADIAN
from internshipfeed.resumes import MAX_PDF_PAGES, MAX_UPLOAD_BYTES
from pdf_fixtures import make_pdf

SOURCE_URL = CANADIAN.url
from internshipfeed.models import JobKeywords, MatchResult
from internshipfeed.normalize import normalize
from internshipfeed.source import parse_readme

FIXTURE = Path(__file__).parent / "fixtures" / "listings.md"


def profile_fit(result):
    return {key: result[key] for key in MatchResult.model_fields}


@unittest.skipUnless(os.environ.get("TEST_DATABASE_URL"), "Set TEST_DATABASE_URL or run scripts/test_with_postgres.py")
class DatabaseTests(unittest.TestCase):
    def setUp(self):
        # Isolate every test in a new schema; never modify existing jobs tables.
        self.schema = "test_" + uuid.uuid4().hex
        with psycopg.connect(os.environ["TEST_DATABASE_URL"], autocommit=True) as conn:
            conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(self.schema)))
        self.dsn = make_conninfo(os.environ["TEST_DATABASE_URL"], options="-csearch_path=" + self.schema)
        original_connect = db.connect
        self.connect = lambda: original_connect(self.dsn)
        self.conn = self.connect()
        db.initialize(self.conn)
        # Advisory locks are database-wide, unlike the per-test schema. Keep the
        # real lock behavior while allowing tests beside a running application sync.
        sync_lock = -int(uuid.uuid4().hex[:15], 16)
        for module in ('internshipfeed.ingestion', 'internshipfeed.sync', 'internshipfeed.api'):
            lock_patch = patch(module + '.SYNC_LOCK', sync_lock)
            lock_patch.start()
            self.addCleanup(lock_patch.stop)
        daily_lock_patch = patch('internshipfeed.daily_refresh.DAILY_REFRESH_LOCK', sync_lock - 1)
        daily_lock_patch.start()
        self.addCleanup(daily_lock_patch.stop)
        self.records = parse_readme(FIXTURE.read_text())
        self.job = self.prepare(normalize(self.records[0], SOURCE_URL, "a"))

    def prepare(self, job, conn=None):
        conn = conn or self.conn
        source = db.ensure_source(conn, job.source)
        snapshot = db.save_snapshot(conn, source['id'], FIXTURE.read_text())
        return job.model_copy(update={'source_id': source['id'], 'snapshot_id': str(snapshot['id'])})

    def save(self, job):
        return db.save_job(self.conn, self.prepare(job))

    def frontend_entry(self):
        # Backend route tests must not depend on ignored local frontend builds.
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        entry = Path(directory.name) / 'index.html'
        entry.write_text('<div id="root"></div><script src="/profile/assets/app.js"></script>')
        return entry

    def tearDown(self):
        app.dependency_overrides.clear()
        self.conn.close()
        with psycopg.connect(os.environ["TEST_DATABASE_URL"], autocommit=True) as conn:
            conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(self.schema)))

    def test_constraint_and_concurrent_reruns(self):
        def save(_):
            with self.connect() as conn:
                return db.save_job(conn, self.job)
        with ThreadPoolExecutor(max_workers=4) as pool:
            outcomes = list(pool.map(save, range(4)))
        self.assertEqual(outcomes.count("inserted"), 1)
        self.assertEqual(outcomes.count("skipped_duplicates"), 3)
        self.assertEqual(len(db.list_source_jobs(self.conn, include_inactive=True)), 1)
        with self.assertRaises(psycopg.errors.UniqueViolation):
            self.conn.execute("INSERT INTO source_postings (source_id, external_id, company, title, location, status, snapshot_id, source_line) VALUES (%s, %s, 'x', 'y', 'z', 'open', %s, 1)", (self.job.source_id, self.job.external_id, int(self.job.snapshot_id)))

    def test_closed_update_preserves_description_url_and_first_seen(self):
        self.job.description = "Experience with Python and React."
        self.job.keywords = JobKeywords(languages=["Python"], frameworks=["React"], requirements=["Experience with Python and React."])
        self.job.enrichment_status = "success"
        self.job.enriched_at = datetime.now(timezone.utc)
        self.assertEqual(self.save(self.job), "inserted")
        before = db.list_source_jobs(self.conn, include_inactive=True)[0]
        closed = normalize(dict(self.records[0], status="closed", raw_url=None), SOURCE_URL, "b")
        self.assertEqual(self.save(closed), "updated")
        after = db.list_source_jobs(self.conn, include_inactive=True)[0]
        self.assertEqual(after.id, before.id)
        self.assertEqual(after.first_seen_at, before.first_seen_at)
        self.assertGreaterEqual(after.last_seen_at, before.last_seen_at)
        self.assertEqual(after.status, "closed")
        self.assertIsNone(after.url)
        self.assertEqual(after.last_known_url, before.url)
        self.assertEqual(after.description, before.description)
        self.assertEqual(after.keywords, before.keywords)
        self.assertEqual(self.save(closed), "skipped_duplicates")
        # Failed refreshes must not erase previously extracted descriptions.
        closed.enrichment_status = "failed"
        closed.enrichment_error = "timeout"
        self.save(closed)
        after = db.list_source_jobs(self.conn, include_inactive=True)[0]
        self.assertEqual(after.description, before.description)
        self.assertEqual(after.enrichment_status, "failed")

    def test_api_uses_database_and_exposes_details(self):
        self.job.description = "Requirements: Python and React"
        self.job.keywords = JobKeywords(frameworks=["React"])
        self.job.enrichment_status = "success"
        self.save(self.job)
        def test_connection():
            with self.connect() as conn:
                yield conn
        app.dependency_overrides[connection] = test_connection
        with patch("internshipfeed.api.FRONTEND_ENTRY", self.frontend_entry()), patch("internshipfeed.api.db.connect", side_effect=self.connect), TestClient(app) as client:
            response = client.get("/api/jobs")
            self.assertEqual(response.status_code, 200)
            data = response.json()
            self.assertEqual(len(data), 1)
            detail = client.get("/api/jobs/%s" % data[0]["id"]).json()
            expected_fields = {"id", "company", "title", "location", "application_url", "posted_at", "status"}
            self.assertEqual(set(data[0]), expected_fields)
            self.assertEqual(set(detail), expected_fields)
            self.assertEqual(detail["application_url"], self.job.url)
            context = client.get("/api/jobs/%s/details" % detail["id"]).json()
            self.assertEqual(set(context["job"]), expected_fields)
            self.assertEqual(context["description"], self.job.description)
            self.assertEqual(context["keywords"]["frameworks"], ["React"])
            self.assertEqual(client.get("/api/feed").json()[0]["job"], detail)
            self.assertEqual(client.get("/api/jobs?status=closed").json(), [])
            self.assertEqual(len(client.get("/api/jobs?q=react").json()), 1)
            self.assertEqual(client.get("/api/jobs?q=%25").json(), [])
            self.assertEqual(client.get("/api/jobs?status=invalid").status_code, 422)
            self.assertEqual(client.get("/api/jobs/999999").status_code, 404)
            self.assertEqual(client.get("/api/health").status_code, 200)
            self.assertEqual(client.get("/").status_code, 200)
            self.assertEqual(client.get("/web/app.js").status_code, 200)
            self.assertEqual(client.get("/data/jobs.json").status_code, 404)

    def test_latest_resume_returns_only_candidate_scoped_metadata(self):
        candidate = db.development_profile(self.conn)['id']
        other = self.conn.execute('INSERT INTO candidate_profiles DEFAULT VALUES RETURNING id').fetchone()['id']
        db.save_resume_text(self.conn, other, 'other.pdf', 'Private other candidate text')
        with patch('internshipfeed.api.db.connect', side_effect=self.connect), TestClient(app) as client:
            self.assertIsNone(client.get('/api/resumes/latest').json())
            db.save_resume_text(self.conn, candidate, 'first.pdf', 'Private candidate text')
            latest = db.save_resume_text(self.conn, candidate, 'latest.pdf', 'Private candidate text')
            response = client.get('/api/resumes/latest')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(set(response.json()), {'id', 'candidate_profile_id', 'filename', 'uploaded_at'})
            self.assertEqual(response.json()['id'], latest['id'])
            self.assertNotIn('Private', response.text)
        with patch('internshipfeed.api.db.connect', side_effect=self.connect), TestClient(app) as client:
            self.assertEqual(client.get('/api/resumes/latest').json()['filename'], 'latest.pdf')

    def test_resume_skills_are_candidate_scoped_and_do_not_mutate_profile(self):
        candidate = db.development_profile(self.conn)['id']
        other = self.conn.execute('INSERT INTO candidate_profiles DEFAULT VALUES RETURNING id').fetchone()['id']
        foreign = db.save_resume_text(self.conn, other, 'other.pdf', 'Private Python')
        before = db.development_profile(self.conn)
        with patch('internshipfeed.api.db.connect', side_effect=self.connect), TestClient(app) as client:
            uploaded = client.post('/api/resumes', files={'file': ('skills.pdf', make_pdf(['Python FastAPI Postgres K8s']), 'application/pdf')})
            self.assertEqual(uploaded.status_code, 201)
            resume_id = uploaded.json()['id']
            response = client.get('/api/resumes/%s/skill-suggestions' % resume_id)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()['skills'], ['FastAPI', 'Kubernetes', 'PostgreSQL', 'Python'])
            self.assertEqual(response.json()['aliases']['postgres'], 'postgresql')
            self.assertNotIn('extracted_text', response.json())
            self.assertEqual(client.get('/api/resumes/%s/skill-suggestions' % foreign['id']).status_code, 404)
            self.assertEqual(client.get('/api/resumes/999999/skill-suggestions').status_code, 404)
            with patch('internshipfeed.api.extract_resume_skills', side_effect=RuntimeError('Private resume text')):
                with self.assertLogs('internshipfeed.api', level='WARNING') as logs:
                    failed = client.get('/api/resumes/%s/skill-suggestions' % resume_id)
                self.assertEqual(failed.status_code, 503)
                self.assertNotIn('Private', failed.text + str(logs.output))
            self.assertEqual(client.get('/api/resumes/latest').json()['id'], resume_id)
            self.assertEqual(db.development_profile(self.conn), before)

    def test_all_product_routes_serve_the_shared_react_app(self):
        with patch('internshipfeed.api.FRONTEND_ENTRY', self.frontend_entry()), patch('internshipfeed.api.db.connect', side_effect=self.connect), TestClient(app) as client:
            for path in ('/', '/recommendations', '/listings', '/feed', '/saved', '/applied', '/dismissed', '/dismissed/', '/profile', '/listings/'):
                response = client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertIn('/profile/assets/', response.text)
                self.assertNotIn('id="jobs"', response.text)

    def test_missing_frontend_build_returns_clean_503(self):
        missing = self.frontend_entry().parent / 'missing.html'
        with patch('internshipfeed.api.FRONTEND_ENTRY', missing), patch('internshipfeed.api.db.connect', side_effect=self.connect), TestClient(app) as client:
            for path in ('/', '/listings', '/profile', '/saved', '/applied', '/dismissed'):
                response = client.get(path)
                self.assertEqual(response.status_code, 503)
                self.assertIn('Build the frontend first.', response.json()['detail'])

    def daily_options(self, directory, canadian=None):
        return {'input_paths': {'canadian-tech': canadian or FIXTURE, 'simplify': FIXTURE.parent / 'simplify.md'},
                'output': directory, 'enrich': False}

    def test_daily_new_ids_exclude_preexisting_updated_jobs_and_reruns(self):
        from internshipfeed.daily_refresh import DailySettings, run_daily_refresh
        from internshipfeed.notifications import EmailReceipt
        self.save(self.job)
        old_id = db.list_jobs(self.conn)[0].id
        with tempfile.TemporaryDirectory() as directory, patch('internshipfeed.db.connect', side_effect=self.connect), \
             patch('internshipfeed.daily_refresh.send_email', return_value=EmailReceipt('145ea166-6aca-43d3-b749-c203049cc9d1')) as send:
            first = run_daily_refresh(DailySettings(0), ingestion_options=self.daily_options(directory))
            batch = db.get_batch(self.conn, first['batch_id'])
            self.assertNotIn(old_id, batch['new_canonical_job_ids'])
            self.assertGreater(first['new_jobs'], 0)
            self.assertEqual(first['exit_code'], 0)
            self.assertEqual(send.call_count, 1)
            send.reset_mock()
            second = run_daily_refresh(DailySettings(0), ingestion_options=self.daily_options(directory))
            self.assertEqual(second['new_jobs'], 0)
            self.assertEqual(second['notified_jobs'], 0)
            send.assert_not_called()
        self.assertFalse(self.conn.execute('SELECT 1 FROM job_notifications WHERE canonical_job_id=%s', (old_id,)).fetchone())

    def test_daily_two_sources_notify_one_canonical_and_record_only_after_send(self):
        from internshipfeed.daily_refresh import DailySettings, run_daily_refresh
        from internshipfeed.notifications import EmailReceipt
        from psycopg.pq import TransactionStatus
        opened = []
        def connect():
            connection = self.connect()
            opened.append(connection)
            return connection
        def send(message, config):
            self.assertIn('InternshipFeed', message.subject)
            self.assertEqual(self.conn.execute('SELECT count(*) AS n FROM job_notifications').fetchone()['n'], 0)
            self.assertGreater(self.conn.execute('SELECT count(*) AS n FROM canonical_jobs').fetchone()['n'], 0)
            self.assertTrue(all(connection.info.transaction_status == TransactionStatus.IDLE for connection in opened if not connection.closed))
            return EmailReceipt('145ea166-6aca-43d3-b749-c203049cc9d1')
        with tempfile.TemporaryDirectory() as directory, patch('internshipfeed.db.connect', side_effect=connect), \
             patch('internshipfeed.daily_refresh.send_email', side_effect=send):
            result = run_daily_refresh(DailySettings(0), ingestion_options=self.daily_options(directory))
        shared = db.get_source_job(self.conn, self.job.source, self.job.external_id).canonical_job_id
        self.assertEqual(len(db.get_job_details(self.conn, shared).sources), 2)
        self.assertEqual(self.conn.execute('SELECT count(*) AS n FROM job_notifications WHERE canonical_job_id=%s', (shared,)).fetchone()['n'], 1)
        rows = self.conn.execute('SELECT * FROM job_notifications').fetchall()
        self.assertEqual(len(rows), result['notified_jobs'])
        self.assertTrue(all(row['channel'] == 'email' and row['sent_at'] is not None for row in rows))

    def test_daily_reuses_recommendation_order_final_threshold_and_top_n(self):
        from internshipfeed.daily_refresh import DailySettings, run_daily_refresh
        from internshipfeed.models import CandidateProfileUpdate
        from internshipfeed.notifications import EmailReceipt
        db.update_development_profile(self.conn, CandidateProfileUpdate(target_roles=['Software Engineering'], skills=[],
            preferred_locations=['Toronto'], preferred_terms=['Winter 2027'], graduation_year=None))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'new.md'
            rows = ['| C%s | Software Engineer Intern (Winter 2027) | Toronto | [Apply](https://example.com/daily/%s) | Oct 2, 2026 |' % (index, index) for index in range(10)]
            path.write_text('\n'.join(FIXTURE.read_text().splitlines()[:3] + rows))
            with patch('internshipfeed.db.connect', side_effect=self.connect), \
                 patch('internshipfeed.daily_refresh.send_email', return_value=EmailReceipt('145ea166-6aca-43d3-b749-c203049cc9d1')) as send:
                result = run_daily_refresh(DailySettings(60, 3), ingestion_options=self.daily_options(directory, path))
                with TestClient(app) as client:
                    recommended = client.get('/api/recommendations').json()
                below_threshold = run_daily_refresh(DailySettings(68, 3), dry_run=True, batch_id=result['batch_id'])
        expected = [row for row in recommended if row['final_score'] >= 60][:3]
        self.assertEqual(result['eligible_jobs'], 10)
        self.assertEqual(result['notified_jobs'], 3)
        self.assertEqual({row['canonical_job_id'] for row in self.conn.execute('SELECT canonical_job_id FROM job_notifications')}, {row['job']['id'] for row in expected})
        message = send.call_args.args[0].text
        positions = [message.index(row['job']['application_url']) for row in expected]
        self.assertEqual(positions, sorted(positions))
        self.assertTrue(all(row['final_score'] == 65 for row in expected))
        self.assertEqual(below_threshold['eligible_jobs'], 0)  # Profile 70 must not pass a final-score threshold of 68.

    def test_new_canonical_ids_are_tracked_even_when_source_first_seen_is_old(self):
        from internshipfeed.ingestion import run_ingestion
        self.save(self.job)
        old_id = db.list_jobs(self.conn)[0].id
        self.conn.execute("UPDATE source_postings SET first_seen_at='2020-01-01T00:00:00Z'")
        original_normalize = normalize
        def corrected(record, source, snapshot):
            job = original_normalize(record, source, snapshot)
            if source == self.job.source and job.external_id == self.job.external_id:
                job.url = 'https://example.com/corrected-application'
            return job
        with tempfile.TemporaryDirectory() as directory, patch('internshipfeed.db.connect', side_effect=self.connect), \
             patch('internshipfeed.ingestion.normalize', side_effect=corrected):
            run = run_ingestion(**self.daily_options(directory))
        job = db.get_source_job(self.conn, self.job.source, self.job.external_id)
        self.assertNotEqual(job.canonical_job_id, old_id)
        first_seen = self.conn.execute('SELECT first_seen_at FROM canonical_jobs WHERE id=%s', (job.canonical_job_id,)).fetchone()['first_seen_at']
        self.assertEqual(first_seen.astimezone(timezone.utc), datetime(2020, 1, 1, tzinfo=timezone.utc))
        self.assertIn(job.canonical_job_id, run['new_canonical_job_ids'])
        self.assertNotIn(old_id, run['new_canonical_job_ids'])

    def test_daily_ranking_includes_existing_behavior_preferences(self):
        from internshipfeed.daily_refresh import DailySettings, run_daily_refresh
        from internshipfeed.models import CandidateProfileUpdate
        candidate = db.development_profile(self.conn)['id']
        db.update_development_profile(self.conn, CandidateProfileUpdate(target_roles=['Software Engineering'], skills=[],
            preferred_locations=['Toronto'], preferred_terms=['Winter 2027'], graduation_year=None))
        history = self.job.model_copy(update={'external_id': 'history', 'url': 'https://example.com/history',
            'title': 'Backend Software Engineer Intern', 'location': 'Toronto', 'term': 'Winter 2027'})
        self.save(history)
        history_id = db.get_source_job(self.conn, history.source, history.external_id).canonical_job_id
        db.record_interaction(self.conn, candidate, history_id, 'save')
        db.record_interaction(self.conn, candidate, history_id, 'applied')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'behavior.md'
            rows = ['| C%s | %s Software Engineer Intern (Winter 2027) | Toronto | [Apply](https://example.com/new/%s) | Oct 2, 2026 |' % (index, family, index)
                    for index, family in enumerate(('Backend', 'Frontend'))]
            path.write_text('\n'.join(FIXTURE.read_text().splitlines()[:3] + rows))
            with patch('internshipfeed.db.connect', side_effect=self.connect):
                result = run_daily_refresh(DailySettings(60, 2), dry_run=True, ingestion_options=self.daily_options(directory, path))
                with TestClient(app) as client:
                    ranked = client.get('/api/recommendations').json()
        first, second = ranked[:2]
        self.assertEqual(first['score'], second['score'])
        self.assertGreater(first['behavior_score'], second['behavior_score'])
        self.assertIn('Backend', first['job']['title'])
        self.assertLess(result['message'].index(first['job']['application_url']), result['message'].index(second['job']['application_url']))

    def test_daily_no_matches_does_not_send_or_record_notifications(self):
        from internshipfeed.daily_refresh import DailySettings, run_daily_refresh
        with tempfile.TemporaryDirectory() as directory, patch('internshipfeed.db.connect', side_effect=self.connect), patch('internshipfeed.daily_refresh.send_email') as send:
            result = run_daily_refresh(DailySettings(100), ingestion_options=self.daily_options(directory))
        send.assert_not_called()
        self.assertEqual(result['exit_code'], 0)
        self.assertEqual(result['eligible_jobs'], 0)
        self.assertEqual(self.conn.execute('SELECT count(*) AS n FROM job_notifications').fetchone()['n'], 0)

    def test_daily_email_failure_preserves_ingestion_and_can_retry_retained_batch_once(self):
        from internshipfeed.daily_refresh import DailySettings, run_daily_refresh
        from internshipfeed.notifications import EmailError, EmailReceipt
        with tempfile.TemporaryDirectory() as directory, patch('internshipfeed.db.connect', side_effect=self.connect):
            with patch('internshipfeed.daily_refresh.send_email', side_effect=EmailError('Fixture provider failure')):
                failed = run_daily_refresh(DailySettings(0), ingestion_options=self.daily_options(directory))
            self.assertEqual((failed['status'], failed['exit_code']), ('email_failed', 1))
            self.assertGreater(len(db.list_jobs(self.conn)), 0)
            self.assertEqual(self.conn.execute('SELECT count(*) AS n FROM job_notifications').fetchone()['n'], 0)
            with patch('internshipfeed.daily_refresh.run_ingestion') as ingest, \
                 patch('internshipfeed.daily_refresh.send_email', return_value=EmailReceipt('145ea166-6aca-43d3-b749-c203049cc9d1')) as send:
                retried = run_daily_refresh(DailySettings(0), batch_id=failed['batch_id'])
                again = run_daily_refresh(DailySettings(0), batch_id=failed['batch_id'])
                ingest.assert_not_called()
                self.assertEqual(send.call_count, 1)
            self.assertGreater(retried['notified_jobs'], 0)
            self.assertEqual(again['notified_jobs'], 0)

    def test_daily_dry_run_and_cli_batch_preview_do_not_send_or_record(self):
        import io
        from contextlib import redirect_stdout
        from internshipfeed.daily_refresh import DailySettings, main as daily_main, run_daily_refresh
        with tempfile.TemporaryDirectory() as directory, patch('internshipfeed.db.connect', side_effect=self.connect), patch('internshipfeed.daily_refresh.send_email') as send:
            result = run_daily_refresh(DailySettings(0), dry_run=True, ingestion_options=self.daily_options(directory))
            self.assertEqual(result['status'], 'dry_run')
            self.assertIn('https://', result['message'])
            with patch.dict(os.environ, {'DAILY_EMAIL_MIN_SCORE': '0'}), redirect_stdout(io.StringIO()) as output:
                code = daily_main(['--dry-run', '--no-ingest', '--batch-id', result['batch_id']])
            self.assertEqual(code, 0)
            self.assertIn(result['message'], output.getvalue())
        send.assert_not_called()
        self.assertEqual(self.conn.execute('SELECT count(*) AS n FROM job_notifications').fetchone()['n'], 0)

    def test_daily_partial_failure_notifies_successful_source_but_reports_nonzero(self):
        from internshipfeed.daily_refresh import DailySettings, run_daily_refresh
        from internshipfeed.notifications import EmailReceipt
        with tempfile.TemporaryDirectory() as directory, patch('internshipfeed.db.connect', side_effect=self.connect), \
             patch('internshipfeed.ingestion.fetch_text', side_effect=RuntimeError('Fixture unavailable')), \
             patch('internshipfeed.daily_refresh.send_email', return_value=EmailReceipt('145ea166-6aca-43d3-b749-c203049cc9d1')) as send:
            result = run_daily_refresh(DailySettings(0), ingestion_options={'output': directory, 'enrich': False,
                'input_paths': {'simplify': FIXTURE.parent / 'simplify.md'}})
        self.assertEqual((result['status'], result['exit_code']), ('partial_failure', 1))
        self.assertGreater(result['notified_jobs'], 0)
        send.assert_called_once()
        batch = db.get_batch(self.conn, result['batch_id'])
        self.assertEqual([source['status'] for source in batch['sources']], ['failed', 'success'])

    def test_daily_full_source_failure_never_sends(self):
        from internshipfeed.daily_refresh import DailySettings, run_daily_refresh
        with tempfile.TemporaryDirectory() as directory, patch('internshipfeed.db.connect', side_effect=self.connect), \
             patch('internshipfeed.ingestion.fetch_text', side_effect=RuntimeError('Fixture unavailable')), \
             patch('internshipfeed.daily_refresh.send_email') as send:
            result = run_daily_refresh(DailySettings(0), ingestion_options={'output': directory, 'enrich': False})
        self.assertEqual((result['status'], result['exit_code']), ('failed', 1))
        self.assertEqual(db.get_batch(self.conn, result['batch_id'])['source_failures'], 2)
        send.assert_not_called()

    def test_daily_retries_apply_current_workflow_filtering(self):
        from internshipfeed.daily_refresh import DailySettings, run_daily_refresh
        with tempfile.TemporaryDirectory() as directory, patch('internshipfeed.db.connect', side_effect=self.connect):
            initial = run_daily_refresh(DailySettings(0), dry_run=True, ingestion_options=self.daily_options(directory))
            candidate = db.development_profile(self.conn)['id']
            for index, details in enumerate(db.list_job_details(self.conn)):
                db.record_interaction(self.conn, candidate, details.job.id, 'dismiss' if index % 2 else 'applied')
            with patch('internshipfeed.daily_refresh.send_email') as send:
                result = run_daily_refresh(DailySettings(0), batch_id=initial['batch_id'])
        send.assert_not_called()
        self.assertEqual(result['eligible_jobs'], 0)

    def test_email_migration_preserves_historical_notification_rows(self):
        self.save(self.job)
        job_id = db.list_jobs(self.conn)[0].id
        candidate = db.development_profile(self.conn)['id']
        self.conn.execute('DROP TABLE job_notifications')
        self.conn.execute((db.ROOT / 'internshipfeed/migrations/009_job_notifications.sql').read_text())
        self.conn.execute("DELETE FROM schema_migrations WHERE version='010_email_notifications.sql'")
        self.conn.execute("INSERT INTO job_notifications (candidate_profile_id, canonical_job_id, channel, provider_message_sid) VALUES (%s,%s,'sms','historical-receipt')", (candidate, job_id))
        db.initialize(self.conn)
        row = self.conn.execute('SELECT * FROM job_notifications').fetchone()
        self.assertEqual((row['channel'], row['provider_message_id']), ('sms', 'historical-receipt'))
        self.assertEqual(db.unnotified_job_ids(self.conn, candidate, [job_id]), [job_id])
        db.record_job_notifications(self.conn, candidate, [job_id], 'email-receipt')
        self.assertEqual(db.unnotified_job_ids(self.conn, candidate, [job_id]), [])
        self.assertEqual(self.conn.execute('SELECT count(*) AS n FROM job_notifications').fetchone()['n'], 2)

    def test_notification_uniqueness_and_deduplication_follow_canonical_merges(self):
        records = [self.job.model_copy(update={'external_id': str(index), 'url': 'https://example.com/notify/' + str(index)}) for index in range(2)]
        for record in records:
            self.save(record)
        a, b = [db.get_source_job(self.conn, record.source, record.external_id).canonical_job_id for record in records]
        candidate = db.development_profile(self.conn)['id']
        db.record_job_notifications(self.conn, candidate, [b], 'SM' + 'a' * 32)
        self.assertEqual(db.record_job_notifications(self.conn, candidate, [b], 'SM' + 'b' * 32), [])
        self.save(records[1].model_copy(update={'resolved_url': records[0].url, 'description': 'Verified role', 'enrichment_status': 'success'}))
        self.assertEqual(db.unnotified_job_ids(self.conn, candidate, [a, b]), [])
        other = self.conn.execute('INSERT INTO candidate_profiles DEFAULT VALUES RETURNING id').fetchone()['id']
        self.assertEqual(db.unnotified_job_ids(self.conn, other, [a, b]), [a])
        self.assertEqual(self.conn.execute('SELECT canonical_job_id FROM job_notifications').fetchone()['canonical_job_id'], b)

    def test_daily_command_lock_prevents_concurrent_ingestion_and_email(self):
        from internshipfeed.daily_refresh import DAILY_REFRESH_LOCK, DailySettings, run_daily_refresh
        self.conn.execute('SELECT pg_advisory_lock(%s)', (DAILY_REFRESH_LOCK,))
        try:
            with patch('internshipfeed.db.connect', side_effect=self.connect), patch('internshipfeed.daily_refresh.run_ingestion') as ingest, patch('internshipfeed.daily_refresh.send_email') as send:
                result = run_daily_refresh(DailySettings(0))
            self.assertEqual(result['status'], 'busy')
            ingest.assert_not_called()
            send.assert_not_called()
        finally:
            self.conn.execute('SELECT pg_advisory_unlock(%s)', (DAILY_REFRESH_LOCK,))

    def test_notification_record_failure_is_nonzero_after_email_without_rollback_of_ingestion(self):
        from internshipfeed.daily_refresh import DailySettings, run_daily_refresh
        from internshipfeed.notifications import EmailReceipt
        with tempfile.TemporaryDirectory() as directory, patch('internshipfeed.db.connect', side_effect=self.connect), \
             patch('internshipfeed.daily_refresh.send_email', return_value=EmailReceipt('145ea166-6aca-43d3-b749-c203049cc9d1')), \
             patch('internshipfeed.db.record_job_notifications', side_effect=psycopg.OperationalError('fixture failure')):
            result = run_daily_refresh(DailySettings(0), ingestion_options=self.daily_options(directory))
        self.assertEqual((result['status'], result['exit_code']), ('notification_record_failed', 1))
        self.assertGreater(len(db.list_jobs(self.conn)), 0)
        self.assertEqual(self.conn.execute('SELECT count(*) AS n FROM job_notifications').fetchone()['n'], 0)

    def test_ingestion_counts_and_persistent_reports(self):
        original_connect = db.connect
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "data"
            # Include an exact duplicate in the source table.
            source = Path(directory) / "README.md"
            source.write_text(FIXTURE.read_text() + FIXTURE.read_text().splitlines()[3] + "\n")
            with patch("internshipfeed.cli.db.connect", side_effect=lambda: original_connect(self.dsn)):
                self.assertEqual(main(["--input", str(source), "--output", str(output), "--no-enrich"]), 0)
                first = json.loads((output / "run.json").read_text())
                self.assertEqual((first["scraped"], first["inserted"], first["skipped_duplicates"], first["failed"]), (4, 3, 1, 0))
                self.assertEqual(main(["--input", str(source), "--output", str(output), "--no-enrich"]), 0)
                second = json.loads((output / "run.json").read_text())
                self.assertEqual((second["inserted"], second["skipped_duplicates"], second["failed"]), (0, 4, 0))
            self.assertEqual(len(db.list_source_jobs(self.conn, include_inactive=True)), 3)
            self.assertEqual(db.latest_batch(self.conn)["id"], second["id"])
            self.assertEqual(self.conn.execute("SELECT count(*) AS n FROM ingestion_runs").fetchone()["n"], 2)

    def test_one_bad_record_does_not_rollback_other_jobs(self):
        original_connect = db.connect
        bad = dict(self.records[0], company=" ")
        with tempfile.TemporaryDirectory() as directory:
            with patch("internshipfeed.cli.db.connect", side_effect=lambda: original_connect(self.dsn)), patch("internshipfeed.sources.parse_readme", return_value=[bad, self.records[1]]):
                result = main(["--input", str(FIXTURE), "--output", directory, "--no-enrich"])
            report = json.loads((Path(directory) / "run.json").read_text())
            self.assertEqual(result, 1)
            self.assertEqual(report["status"], "partial_failure")
            self.assertEqual((report["scraped"], report["inserted"], report["failed"]), (2, 1, 1))
            self.assertEqual(len(db.list_source_jobs(self.conn, include_inactive=True)), 1)

    def test_enrichment_persisted_by_scraper(self):
        original_connect = db.connect
        enrichment = {"status": "success", "error": None, "description": "Experience with Python.", "keywords": {"languages": ["Python"]}, "extraction_method": "json_ld", "resolved_url": self.job.url}
        with tempfile.TemporaryDirectory() as directory:
            with patch("internshipfeed.cli.db.connect", side_effect=lambda: original_connect(self.dsn)), patch("internshipfeed.ingestion.enrich_job", return_value=enrichment):
                self.assertEqual(main(["--input", str(FIXTURE), "--output", directory, "--enrich"]), 0)
            job = next(j for j in db.list_source_jobs(self.conn, include_inactive=True) if j.status == "open")
            self.assertEqual(job.description, enrichment["description"])
            self.assertEqual(job.keywords.languages, ["Python"])
            self.assertIsNotNone(job.enriched_at)


    def test_inactive_jobs_hidden_from_feed_and_details(self):
        for record in self.records:
            self.save(normalize(record, SOURCE_URL, "snapshot"))
        active = db.list_jobs(self.conn)
        self.assertEqual(len(active), 2)
        closed = next(job for job in db.list_source_jobs(self.conn, include_inactive=True) if job.status == "closed")
        self.assertIsNone(db.get_job(self.conn, closed.canonical_job_id))
        self.job.status = "dead"
        self.save(self.job)
        self.assertEqual(len(db.list_jobs(self.conn)), 1)
        self.assertEqual(db.list_jobs(self.conn, status="closed"), [])

    def test_ui_sync_runs_background_and_blocks_duplicate_runs(self):
        from internshipfeed.ingestion import run_ingestion, SYNC_LOCK
        def test_connection():
            with self.connect() as conn:
                yield conn
        app.dependency_overrides[connection] = test_connection
        with tempfile.TemporaryDirectory() as directory:
            def small_workflow(**kwargs):
                return run_ingestion(input_path=FIXTURE, output=directory, enrich=False, **kwargs)
            with patch("internshipfeed.api.db.connect", side_effect=self.connect), patch("internshipfeed.sync.run_ingestion", side_effect=small_workflow), TestClient(app) as client:
                response = client.post("/api/sync")
                self.assertEqual(response.status_code, 202)
                run_id = response.json()["id"]
                for _ in range(100):
                    report = client.get("/api/ingestion-batches/" + run_id).json()
                    if report["status"] != "running":
                        break
                    time.sleep(0.02)
                self.assertEqual(report["status"], "success")
                self.assertEqual(report["inserted"], 3)
                with self.connect() as lock:
                    lock.execute("SELECT pg_advisory_lock(%s)", (SYNC_LOCK,))
                    self.assertEqual(client.post("/api/sync").json()["id"], run_id)
                self.assertEqual(client.post("/api/sync", headers={"Origin": "https://evil.example"}).status_code, 403)


    def test_two_sources_share_database_and_repeat_without_duplicates(self):
        from internshipfeed.ingestion import run_ingestion
        from internshipfeed.sources import CANADIAN, SIMPLIFY
        original_connect = db.connect
        inputs = {'canadian-tech': FIXTURE, 'simplify': FIXTURE.parent / 'simplify.md'}
        with tempfile.TemporaryDirectory() as directory, patch('internshipfeed.ingestion.db.connect', side_effect=lambda: original_connect(self.dsn)):
            first = run_ingestion(input_paths=inputs, output=directory, enrich=False)
            self.assertEqual(first['status'], 'success')
            self.assertEqual((first['scraped'], first['inserted']), (6, 6))
            self.assertEqual(len(first['sources']), 2)
            second = run_ingestion(input_paths=inputs, output=directory, enrich=False)
            self.assertEqual((second['inserted'], second['skipped_duplicates']), (0, 6))
        stored = db.list_source_jobs(self.conn, include_inactive=True)
        self.assertEqual({job.source for job in stored}, {CANADIAN.url, SIMPLIFY.url})
        self.assertEqual(len(stored), 6)
        self.assertEqual(len(db.list_jobs(self.conn, source=SIMPLIFY.url)), 2)

    def test_source_failure_does_not_prevent_other_source_import(self):
        from internshipfeed.ingestion import run_ingestion
        original_connect = db.connect
        with tempfile.TemporaryDirectory() as directory:
            broken = Path(directory) / 'broken.md'
            broken.write_text('Upstream table format changed')
            with patch('internshipfeed.ingestion.db.connect', side_effect=lambda: original_connect(self.dsn)):
                run = run_ingestion(input_paths={'canadian-tech': broken, 'simplify': FIXTURE.parent / 'simplify.md'}, output=directory, enrich=False)
        self.assertEqual(run['status'], 'partial_failure')
        self.assertEqual(run['source_failures'], 1)
        self.assertEqual(run['inserted'], 3)
        self.assertEqual(run['sources'][0]['status'], 'failed')
        self.assertEqual(run['sources'][1]['status'], 'success')

    def test_removed_configuration_endpoints_are_absent(self):
        with patch('internshipfeed.api.db.connect', side_effect=self.connect), TestClient(app) as client:
            self.assertEqual(client.get('/api/settings/ai').status_code, 404)
            self.assertEqual(len(client.get('/api/sources').json()), 2)

    def test_url_match_links_two_source_identities_to_one_canonical_job(self):
        from internshipfeed.sources import SIMPLIFY
        first = self.job.model_copy(update={'url': 'https://jobs.example.com/job/123?utm_source=canadian'})
        second = self.job.model_copy(update={'source': SIMPLIFY.url, 'external_id': 'simplify:875',
                                           'title': 'SWE Co-op', 'url': 'http://JOBS.example.com/job/123/?ref=Simplify'})
        self.save(first)
        self.save(second)
        a = db.get_source_job(self.conn, first.source, first.external_id)
        b = db.get_source_job(self.conn, second.source, second.external_id)
        self.assertNotEqual(a.id, b.id)
        self.assertNotEqual(a.external_id, b.external_id)
        self.assertEqual(a.canonical_job_id, b.canonical_job_id)
        listings = db.list_jobs(self.conn)
        self.assertEqual(len(listings), 1)
        self.assertEqual(listings[0].id, a.canonical_job_id)
        self.assertEqual({record.id for record in db.get_job_details(self.conn, listings[0].id).sources}, {a.id, b.id})
        self.assertEqual(db.reconciliation_stats(self.conn)['duplicate_listings_removed'], 1)
        self.assertEqual(len(db.list_jobs(self.conn, source=SIMPLIFY.url)), 1)
        self.assertEqual(len(db.list_jobs(self.conn, query='SWE Co-op')), 1)
        self.assertEqual(self.save(second), 'skipped_duplicates')
        self.assertEqual(db.get_source_job(self.conn, second.source, second.external_id).canonical_job_id, a.canonical_job_id)

    def test_same_title_different_or_missing_urls_not_merged(self):
        first = self.job.model_copy(update={'url': 'https://stripe.com/jobs/search?gh_jid=123'})
        second = first.model_copy(update={'source': 'source-b', 'external_id': '875', 'url': 'https://stripe.com/jobs/search?gh_jid=456'})
        third = first.model_copy(update={'source': 'source-c', 'external_id': '125', 'url': None})
        fourth = third.model_copy(update={'source': 'source-d'})
        for job in (first, second, third, fourth):
            self.save(job)
        self.assertEqual(len(db.list_jobs(self.conn)), 4)
        self.assertEqual(self.conn.execute('SELECT count(*) AS n FROM source_postings').fetchone()['n'], 4)

    def test_closure_retains_canonical_mapping_and_active_peer(self):
        first = self.job.model_copy()
        second = first.model_copy(update={'source': 'source-b', 'external_id': '875'})
        for job in (first, second):
            self.save(job)
        initial = db.get_source_job(self.conn, first.source, first.external_id)
        closed = first.model_copy(update={'status': 'closed', 'url': None})
        self.save(closed)
        after = db.get_source_job(self.conn, first.source, first.external_id)
        self.assertEqual(after.id, initial.id)
        self.assertEqual(after.canonical_job_id, initial.canonical_job_id)
        listing = db.get_job(self.conn, initial.canonical_job_id)
        self.assertEqual(listing.status, 'open')
        self.assertEqual(len(db.get_job_details(self.conn, listing.id).sources), 2)
        self.assertEqual(db.list_jobs(self.conn, source=first.source), [])
        self.save(second.model_copy(update={'status': 'closed', 'url': None}))
        self.assertEqual(db.list_jobs(self.conn), [])
        self.assertIsNone(db.get_job(self.conn, initial.canonical_job_id))

    def test_url_correction_detaches_only_the_corrected_source(self):
        first = self.job.model_copy()
        second = first.model_copy(update={'source': 'source-b', 'external_id': '875'})
        self.save(first)
        self.save(second)
        before = db.get_source_job(self.conn, second.source, second.external_id)
        changed = second.model_copy(update={'url': 'https://example.com/jobs/different'})
        self.save(changed)
        after = db.get_source_job(self.conn, second.source, second.external_id)
        self.assertEqual(after.id, before.id)
        self.assertEqual(after.first_seen_at, before.first_seen_at)
        self.assertNotEqual(after.canonical_job_id, before.canonical_job_id)
        self.assertEqual(db.get_source_job(self.conn, first.source, first.external_id).canonical_job_id, before.canonical_job_id)
        self.assertEqual(len(db.list_jobs(self.conn)), 2)

    def test_verified_redirect_bridges_existing_groups_and_old_ids_resolve(self):
        direct = self.job.model_copy(update={'url': 'https://employer.example/jobs/123'})
        wrapper = direct.model_copy(update={'source': 'source-b', 'external_id': '875', 'url': 'https://simplify.jobs/p/abc'})
        self.save(direct)
        self.save(wrapper)
        old_id = db.get_source_job(self.conn, wrapper.source, wrapper.external_id).canonical_job_id
        wrapper.enrichment_status = 'success'
        wrapper.description = 'Build software with Python.'
        wrapper.resolved_url = direct.url
        self.save(wrapper)
        self.assertEqual(len(db.list_jobs(self.conn)), 1)
        current = db.list_jobs(self.conn)[0]
        self.assertEqual(db.get_job(self.conn, old_id).id, current.id)
        self.assertEqual(db.get_job_details(self.conn, current.id).description, wrapper.description)
        self.assertEqual(len(db.get_job_details(self.conn, current.id).sources), 2)

    def test_concurrent_cross_source_inserts_create_one_canonical(self):
        def save(index):
            job = self.job.model_copy(update={'source': 'source-%s' % index, 'external_id': str(index),
                                              'url': 'https://example.com/job/123?utm_source=%s' % index})
            with self.connect() as conn:
                return db.save_job(conn, self.prepare(job, conn))
        with ThreadPoolExecutor(max_workers=4) as pool:
            self.assertEqual(list(pool.map(save, range(4))), ['inserted'] * 4)
        self.assertEqual(len(db.list_source_jobs(self.conn)), 4)
        self.assertEqual(len(db.list_jobs(self.conn)), 1)
        self.assertEqual(self.conn.execute('SELECT count(*) AS n FROM canonical_jobs').fetchone()['n'], 1)


    def test_generic_career_url_does_not_merge_unrelated_source_records(self):
        first = self.job.model_copy(update={'url': 'https://example.com/careers'})
        second = first.model_copy(update={'source': 'source-b', 'external_id': '875'})
        self.save(first)
        self.save(second)
        self.assertEqual(len(db.list_jobs(self.conn)), 2)


    def test_legacy_database_backfill_preserves_ids_and_is_idempotent(self):
        legacy_schema = 'legacy_' + uuid.uuid4().hex
        with psycopg.connect(os.environ['TEST_DATABASE_URL'], autocommit=True) as admin:
            admin.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(legacy_schema)))
        legacy_dsn = make_conninfo(os.environ['TEST_DATABASE_URL'], options='-csearch_path=' + legacy_schema)
        try:
            with db.connect(legacy_dsn) as legacy:
                legacy.execute((Path(db.__file__).parent / 'migrations' / '001_jobs.sql').read_text())
                for source, url in (('source-a', 'https://example.com/job/123'), ('source-b', 'http://EXAMPLE.com/job/123/?utm_source=github')):
                    legacy.execute("INSERT INTO jobs (source, external_id, company, title, location, url, status, snapshot_id, source_line) VALUES (%s, '875', 'Acme', 'Intern', 'Toronto', %s, 'open', 'legacy', 1)", (source, url))
                before = legacy.execute('SELECT id, first_seen_at FROM jobs ORDER BY id').fetchall()
                db.initialize(legacy)
                rows = legacy.execute('SELECT p.id, p.first_seen_at, m.canonical_job_id FROM source_postings p JOIN canonical_job_sources m ON m.source_posting_id=p.id ORDER BY p.id').fetchall()
                self.assertEqual([{key: row[key] for key in ('id', 'first_seen_at')} for row in rows], before)
                self.assertEqual(rows[0]['canonical_job_id'], rows[1]['canonical_job_id'])
                self.assertEqual(len(db.list_jobs(legacy)), 1)
                db.initialize(legacy)
                self.assertIsNone(legacy.execute("SELECT to_regclass('jobs') AS table_name").fetchone()['table_name'])
                self.assertEqual(legacy.execute('SELECT p.id, p.first_seen_at, m.canonical_job_id FROM source_postings p JOIN canonical_job_sources m ON m.source_posting_id=p.id ORDER BY p.id').fetchall(), rows)
        finally:
            with psycopg.connect(os.environ['TEST_DATABASE_URL'], autocommit=True) as admin:
                admin.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(legacy_schema)))


    def test_failed_fetch_on_changed_url_does_not_reuse_old_redirect_or_description(self):
        old = self.job.model_copy(update={'enrichment_status': 'success', 'description': 'Python required.', 'resolved_url': self.job.url})
        peer = self.job.model_copy(update={'source': 'source-b', 'external_id': '875'})
        self.save(old)
        self.save(peer)
        previous = db.get_source_job(self.conn, old.source, old.external_id)
        changed = old.model_copy(update={'url': 'https://example.com/jobs/different', 'enrichment_status': 'failed'})
        self.save(changed)
        stored = db.get_source_job(self.conn, changed.source, changed.external_id)
        self.assertNotEqual(stored.canonical_job_id, previous.canonical_job_id)
        self.assertIsNone(stored.description)
        self.assertIsNone(stored.resolved_url)
        self.assertEqual(len(db.list_jobs(self.conn)), 2)


    def test_sql_only_upgrade_is_completed_without_recreating_legacy_table(self):
        legacy_schema = 'upgrade_' + uuid.uuid4().hex
        with psycopg.connect(os.environ['TEST_DATABASE_URL'], autocommit=True) as admin:
            admin.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(legacy_schema)))
        dsn = make_conninfo(os.environ['TEST_DATABASE_URL'], options='-csearch_path=' + legacy_schema)
        try:
            with db.connect(dsn) as legacy:
                migrations = Path(db.__file__).parent / 'migrations'
                legacy.execute((migrations / '001_jobs.sql').read_text())
                legacy.execute("INSERT INTO jobs (source, external_id, company, title, location, url, status, snapshot_id, source_line) VALUES ('source-a', '875', 'Acme', 'Intern', 'Toronto', 'https://example.com/job/1', 'open', 'legacy', 1)")
                legacy.execute('CREATE TABLE schema_migrations (version TEXT PRIMARY KEY)')
                for path in sorted(migrations.glob('*.sql')):
                    if path.name != '001_jobs.sql':
                        legacy.execute(path.read_text())
                        legacy.execute('INSERT INTO schema_migrations (version) VALUES (%s)', (path.name,))
                self.assertIsNone(legacy.execute('SELECT m.canonical_job_id FROM source_postings p LEFT JOIN canonical_job_sources m ON m.source_posting_id=p.id').fetchone()['canonical_job_id'])
                db.initialize(legacy)
                self.assertIsNotNone(legacy.execute('SELECT m.canonical_job_id FROM source_postings p LEFT JOIN canonical_job_sources m ON m.source_posting_id=p.id').fetchone()['canonical_job_id'])
                self.assertIsNone(legacy.execute("SELECT to_regclass('jobs') AS name").fetchone()['name'])
                self.assertEqual(len(db.list_jobs(legacy)), 1)
        finally:
            with psycopg.connect(os.environ['TEST_DATABASE_URL'], autocommit=True) as admin:
                admin.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(legacy_schema)))


    def test_canonical_storage_persists_requested_metadata(self):
        columns = self.conn.execute("SELECT column_name FROM information_schema.columns WHERE table_schema = current_schema() AND table_name = 'canonical_jobs' ORDER BY ordinal_position").fetchall()
        self.assertEqual([row['column_name'] for row in columns], ['id', 'company', 'title', 'location', 'application_url', 'posted_at', 'status', 'description', 'keywords', 'first_seen_at', 'last_seen_at', 'created_at', 'updated_at'])
        self.save(self.job)
        row = self.conn.execute('SELECT * FROM canonical_jobs').fetchone()
        self.assertEqual(row['application_url'], self.job.url)
        self.assertEqual(row['id'], db.get_source_job(self.conn, self.job.source, self.job.external_id).canonical_job_id)

    def test_all_proposed_relations_and_seed_profile_exist(self):
        tables = {row['table_name'] for row in self.conn.execute("SELECT table_name FROM information_schema.tables WHERE table_schema=current_schema() AND table_type='BASE TABLE'").fetchall()}
        self.assertTrue({'job_sources', 'ingestion_runs', 'source_snapshots', 'source_postings', 'canonical_jobs', 'canonical_job_sources', 'candidate_profiles', 'resumes', 'job_interactions'} <= tables)
        profile = db.development_profile(self.conn)
        self.assertEqual(profile['skills'], [])
        self.assertEqual(self.conn.execute('SELECT count(*) AS n FROM candidate_profiles').fetchone()['n'], 1)
        db.initialize(self.conn)
        self.assertEqual(self.conn.execute('SELECT count(*) AS n FROM candidate_profiles').fetchone()['n'], 1)

    def test_profile_api_returns_seeded_profile(self):
        seeded = db.development_profile(self.conn)
        with patch('internshipfeed.api.db.connect', side_effect=self.connect), TestClient(app) as client:
            response = client.get('/api/profile')
        self.assertEqual(response.status_code, 200)
        profile = response.json()
        self.assertEqual(set(profile), {'id', 'target_roles', 'skills', 'preferred_locations',
                                        'preferred_terms', 'graduation_year', 'created_at', 'updated_at'})
        self.assertEqual(profile['id'], seeded['id'])
        self.assertGreater(profile['id'], 0)
        for key in ('target_roles', 'skills', 'preferred_locations', 'preferred_terms'):
            self.assertIsInstance(profile[key], list)
            self.assertEqual(profile[key], seeded[key])
            self.assertEqual(profile[key], [])
        self.assertIsNone(profile['graduation_year'])
        for key in ('created_at', 'updated_at'):
            self.assertEqual(datetime.fromisoformat(profile[key].replace('Z', '+00:00')), seeded[key])

    def test_profile_full_update_persists_arrays_identity_and_timestamps(self):
        payload = {
            'target_roles': ['Software Engineering', 'Data Engineering'],
            'skills': ['Python', 'SQL'], 'preferred_locations': ['Toronto', 'Ottawa'],
            'preferred_terms': ['Summer 2027'], 'graduation_year': 2027,
        }
        with patch('internshipfeed.api.db.connect', side_effect=self.connect), TestClient(app) as client:
            before = client.get('/api/profile').json()
            response = client.put('/api/profile', json=payload)
            self.assertEqual(response.status_code, 200)
            after = response.json()
            self.assertEqual(client.get('/api/profile').json(), after)
        for key, value in payload.items():
            self.assertEqual(after[key], value)
        self.assertEqual(after['id'], before['id'])
        self.assertEqual(after['created_at'], before['created_at'])
        self.assertGreater(datetime.fromisoformat(after['updated_at'].replace('Z', '+00:00')),
                           datetime.fromisoformat(before['updated_at'].replace('Z', '+00:00')))
        stored = self.conn.execute('SELECT * FROM candidate_profiles WHERE id=%s', (after['id'],)).fetchone()
        for key, value in payload.items():
            self.assertEqual(stored[key], value)
        self.assertEqual(stored['updated_at'], datetime.fromisoformat(after['updated_at'].replace('Z', '+00:00')))
        types = self.conn.execute('SELECT pg_typeof(target_roles)::text AS roles, pg_typeof(skills)::text AS skills, pg_typeof(preferred_locations)::text AS locations, pg_typeof(preferred_terms)::text AS terms FROM candidate_profiles').fetchone()
        self.assertEqual(set(types.values()), {'text[]'})
        db.initialize(self.conn)
        self.assertEqual(self.conn.execute('SELECT count(*) AS n FROM candidate_profiles').fetchone()['n'], 1)
        self.assertEqual(db.development_profile(self.conn), stored)

    def test_profile_update_normalizes_all_lists_before_persistence(self):
        payload = {
            'target_roles': [' Software Engineering ', 'Software Engineering', ''],
            'skills': ['Python', ' Python ', 'SQL', ''],
            'preferred_locations': ['Toronto', ' Toronto '],
            'preferred_terms': ['Summer 2027', 'Summer 2027'], 'graduation_year': 2027,
        }
        expected = {
            'target_roles': ['Software Engineering'], 'skills': ['Python', 'SQL'],
            'preferred_locations': ['Toronto'], 'preferred_terms': ['Summer 2027'], 'graduation_year': 2027,
        }
        with patch('internshipfeed.api.db.connect', side_effect=self.connect), TestClient(app) as client:
            response = client.put('/api/profile', json=payload)
        self.assertEqual(response.status_code, 200)
        stored = db.development_profile(self.conn)
        for key, value in expected.items():
            self.assertEqual(response.json()[key], value)
            self.assertEqual(stored[key], value)

    def test_profile_can_clear_all_preferences_and_graduation_year(self):
        populated = {key: ['example'] for key in ('target_roles', 'skills', 'preferred_locations', 'preferred_terms')}
        populated['graduation_year'] = 2027
        empty = {key: [] for key in ('target_roles', 'skills', 'preferred_locations', 'preferred_terms')}
        empty['graduation_year'] = None
        with patch('internshipfeed.api.db.connect', side_effect=self.connect), TestClient(app) as client:
            first = client.put('/api/profile', json=populated)
            self.assertEqual(first.status_code, 200)
            response = client.put('/api/profile', json=empty)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()['id'], first.json()['id'])
            self.assertEqual(client.get('/api/profile').json(), response.json())
        for key, value in empty.items():
            self.assertEqual(response.json()[key], value)
            self.assertEqual(db.development_profile(self.conn)[key], value)
        self.assertEqual(self.conn.execute('SELECT count(*) AS n FROM candidate_profiles').fetchone()['n'], 1)

    def test_profile_api_rejects_invalid_payloads_without_modifying_row(self):
        payload = {key: [] for key in ('target_roles', 'skills', 'preferred_locations', 'preferred_terms')}
        payload['graduation_year'] = None
        invalid = [{'skills': []}]
        for key in payload:
            invalid.append({field: value for field, value in payload.items() if field != key})
        for key in ('target_roles', 'skills', 'preferred_locations', 'preferred_terms'):
            for value in ('Python', None, [123], [True], [None]):
                invalid.append(dict(payload, **{key: value}))
        for value in (1899, 2201, 3000, '2027', 2027.5, True):
            invalid.append(dict(payload, graduation_year=value))
        for key in ('id', 'created_at', 'updated_at', 'admin', 'favorite_color'):
            invalid.append(dict(payload, **{key: 'unexpected'}))
        before = db.development_profile(self.conn)
        with patch('internshipfeed.api.db.connect', side_effect=self.connect), TestClient(app) as client:
            for body in invalid:
                with self.subTest(payload=body):
                    self.assertEqual(client.put('/api/profile', json=body).status_code, 422)
        self.assertEqual(db.development_profile(self.conn), before)

    def test_profile_missing_fails_clearly_without_creating_another_row(self):
        from internshipfeed.models import CandidateProfileUpdate
        self.conn.execute('DELETE FROM candidate_profiles')
        empty = {key: [] for key in ('target_roles', 'skills', 'preferred_locations', 'preferred_terms')}
        empty['graduation_year'] = None
        self.assertIsNone(db.update_development_profile(self.conn, CandidateProfileUpdate(**empty)))
        with patch('internshipfeed.api.db.connect', side_effect=self.connect), TestClient(app) as client:
            responses = [client.get('/api/profile'), client.put('/api/profile', json=empty)]
        for response in responses:
            self.assertEqual(response.status_code, 503)
            self.assertIn('Development profile is missing', response.json()['detail'])
        self.assertEqual(self.conn.execute('SELECT count(*) AS n FROM candidate_profiles').fetchone()['n'], 0)

    def test_profile_update_reuses_local_mutation_policy(self):
        payload = {key: [] for key in ('target_roles', 'skills', 'preferred_locations', 'preferred_terms')}
        payload['graduation_year'] = None
        before = db.development_profile(self.conn)
        with patch('internshipfeed.api.db.connect', side_effect=self.connect), TestClient(app) as client:
            response = client.put('/api/profile', json=payload, headers={'Origin': 'https://evil.example'})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(db.development_profile(self.conn), before)

    def test_recommendations_components_source_evidence_visibility_and_stable_order(self):
        from internshipfeed.sources import SIMPLIFY
        payload = {'target_roles': ['Backend Engineering'], 'skills': ['Python', 'Postgres', 'Docker'],
                   'preferred_locations': ['Toronto'], 'preferred_terms': ['Summer 2027'], 'graduation_year': 2027}
        primary = self.job.model_copy(update={'external_id': 'recommended-main', 'title': 'General Intern',
                                              'location': 'Unknown', 'term': 'Winter 2027', 'posted_at': date(2026, 10, 15),
                                              'keywords': JobKeywords(languages=['Python'], tools_and_platforms=['PostgreSQL', 'Docker']),
                                              'enrichment_status': 'success'})
        self.save(primary)
        self.save(primary.model_copy(update={'source': SIMPLIFY.url, 'external_id': 'recommended-peer',
                                             'title': 'Backend Software Engineering Intern', 'location': 'Toronto, ON',
                                             'term': 'Summer 2027'}))
        expected_id = db.get_source_job(self.conn, primary.source, primary.external_id).canonical_job_id
        tie_ids = []
        for index, posted in enumerate((date(2026, 10, 1), date(2026, 10, 2), date(2026, 10, 2), None)):
            record = primary.model_copy(update={'external_id': 'tie-' + str(index), 'url': 'https://example.com/job/tie-' + str(index),
                                                 'title': 'Backend Engineer Intern', 'location': 'Vancouver, BC', 'term': None,
                                                 'posted_at': posted, 'keywords': JobKeywords(languages=['Python'], tools_and_platforms=['Docker'])})
            self.save(record)
            tie_ids.append(db.get_source_job(self.conn, record.source, record.external_id).canonical_job_id)
        for status in ('closed', 'dead', 'unknown'):
            self.save(primary.model_copy(update={'external_id': status, 'url': 'https://example.com/job/' + status,
                                                 'status': status, 'title': 'Product Management Intern', 'location': 'Vancouver, BC',
                                                 'keywords': JobKeywords(), 'term': None}))
        before = self.conn.execute('SELECT * FROM canonical_jobs ORDER BY id').fetchall()
        with patch('internshipfeed.api.db.connect', side_effect=self.connect), TestClient(app) as client:
            self.assertEqual(client.put('/api/profile', json=payload).status_code, 200)
            response = client.get('/api/recommendations')
            self.assertEqual(response.status_code, 200)
            results = response.json()
            self.assertEqual([row['score'] for row in results], [100, 70, 70, 70, 70, 0])
            self.assertEqual([row['job']['id'] for row in results[:5]], [expected_id, tie_ids[2], tie_ids[1], tie_ids[0], tie_ids[3]])
            best = results[0]
            self.assertEqual((best['role_score'], best['skills_score'], best['location_score'], best['term_score']), (40, 30, 20, 10))
            self.assertEqual(best['matched_skills'], ['Docker', 'PostgreSQL', 'Python'])
            self.assertEqual(len(best['reasons']), 4)
            self.assertIn('Backend Software Engineering Intern', best['reasons'][0])
            self.assertIn('Toronto, ON', best['reasons'][2])
            self.assertIn('Summer 2027', best['reasons'][3])
            self.assertEqual(set(best['job']), {'id', 'company', 'title', 'location', 'application_url', 'posted_at', 'status'})
            self.assertTrue(all(row['job']['status'] not in {'closed', 'dead'} for row in results))
            self.assertEqual(results[-1]['job']['status'], 'unknown')
            self.assertEqual(client.get('/api/recommendations?min_score=100').json(), [best])
            self.assertEqual(client.get('/api/recommendations').json(), results)
        self.assertEqual(self.conn.execute('SELECT * FROM canonical_jobs ORDER BY id').fetchall(), before)

    def test_recommendation_fit_ignores_resume_and_interactions_while_visibility_changes(self):
        for title, external in [('Backend Engineer Intern', 'backend'), ('Product Management Intern', 'product')]:
            self.save(self.job.model_copy(update={'external_id': external, 'url': 'https://example.com/job/' + external,
                                                  'title': title, 'term': None, 'keywords': JobKeywords()}))
        empty = {'target_roles': [], 'skills': [], 'preferred_locations': [], 'preferred_terms': [], 'graduation_year': None}
        with patch('internshipfeed.api.db.connect', side_effect=self.connect), TestClient(app) as client:
            self.assertEqual([row['score'] for row in client.get('/api/recommendations').json()], [0, 0])
            self.assertEqual(client.put('/api/profile', json=dict(empty, target_roles=['Backend Engineering'])).status_code, 200)
            backend = client.get('/api/recommendations').json()
            self.assertEqual(backend[0]['job']['title'], 'Backend Engineer Intern')
            self.assertEqual([row['score'] for row in backend], [40, 0])
            candidate_id = db.development_profile(self.conn)['id']
            db.save_resume_text(self.conn, candidate_id, 'resume.pdf', 'Product management, Toronto, Summer 2027, Python')
            self.assertEqual(client.get('/api/recommendations').json(), backend)
            db.record_interaction(self.conn, candidate_id, backend[0]['job']['id'], 'dismiss')
            remaining = client.get('/api/recommendations').json()
            self.assertEqual([profile_fit(row) for row in remaining], [profile_fit(row) for row in backend[1:]])
            from internshipfeed.matching import score_job
            from internshipfeed.models import CandidateProfile
            fit = score_job(CandidateProfile.model_validate(db.development_profile(self.conn)), db.get_job_details(self.conn, backend[0]['job']['id']))
            self.assertEqual(fit.model_dump(mode='json'), profile_fit(backend[0]))
            client.put('/api/profile', json=dict(empty, target_roles=['Product Management']))
            product = client.get('/api/recommendations').json()
            self.assertEqual(product[0]['job']['title'], 'Product Management Intern')
            self.assertEqual([row['score'] for row in product], [40])

    def test_behavior_history_bulk_loads_deduplicated_merged_candidate_evidence(self):
        from internshipfeed.behavioral import build_behavior_profile
        candidate = db.development_profile(self.conn)['id']
        ids, records = [], []
        for index in range(3):
            record = self.job.model_copy(update={'external_id': 'behavior-' + str(index), 'url': 'https://example.com/behavior/' + str(index),
                                                 'title': 'Backend Engineer Intern', 'keywords': JobKeywords(languages=['Python'])})
            self.save(record)
            records.append(record)
            ids.append(db.get_source_job(self.conn, record.source, record.external_id).canonical_job_id)
        other = self.conn.execute('INSERT INTO candidate_profiles DEFAULT VALUES RETURNING id').fetchone()['id']
        db.record_interaction(self.conn, other, ids[2], 'dismiss')
        for job_id, actions in ((ids[0], ('save', 'click')), (ids[1], ('save', 'save', 'applied')), (ids[2], ('view',))):
            for action in actions:
                db.record_interaction(self.conn, candidate, job_id, action)
        # A merge must cap repeated signals across historical aliases too.
        self.save(records[1].model_copy(update={'resolved_url': records[0].url, 'enrichment_status': 'success', 'description': 'Readable job'}))
        # Closed observations remain valid preference evidence.
        self.save(records[0].model_copy(update={'status': 'closed', 'url': None}))
        self.save(records[1].model_copy(update={'status': 'closed', 'url': None, 'resolved_url': records[0].url,
                                               'enrichment_status': 'success'}))
        monitored = Mock(wraps=self.conn)
        history = db.behavior_history(monitored, candidate)
        self.assertEqual(monitored.execute.call_count, 3)  # grouped history + bulk jobs + bulk observations
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0].details.job.id, ids[0])
        self.assertEqual(history[0].details.job.status, 'closed')
        self.assertEqual(history[0].interaction_types, frozenset({'save', 'click', 'applied'}))
        learned = build_behavior_profile(history)
        self.assertEqual(learned.roles, {'backend engineering': 7})
        self.assertEqual(learned.skills, {'python': 7})
        self.assertEqual(db.behavior_history(self.conn, other)[0].interaction_types, frozenset({'dismiss'}))
        self.assertEqual(self.conn.execute('SELECT count(*) AS n FROM job_interactions').fetchone()['n'], 7)

    def test_recommendations_personalize_order_from_persisted_history_without_changing_fit_or_feed(self):
        ids, records = [], []
        for index, title in enumerate(('Backend Software Engineer Intern', 'Backend Software Engineer Intern', 'Frontend Software Engineer Intern')):
            record = self.job.model_copy(update={'external_id': 'rank-' + str(index), 'url': 'https://example.com/rank/' + str(index),
                                                 'title': title, 'keywords': JobKeywords(languages=['Python', 'SQL']), 'term': 'Summer 2027'})
            self.save(record)
            records.append(record)
            ids.append(db.get_source_job(self.conn, record.source, record.external_id).canonical_job_id)
        history, backend, frontend = ids
        candidate = db.development_profile(self.conn)['id']
        before_jobs = self.conn.execute('SELECT * FROM canonical_jobs ORDER BY id').fetchall()
        with patch('internshipfeed.api.db.connect', side_effect=self.connect), TestClient(app) as client:
            client.put('/api/profile', json={'target_roles': ['Software Engineering'], 'skills': [], 'preferred_locations': [],
                                            'preferred_terms': [], 'graduation_year': None})
            original = client.get('/api/recommendations').json()
            self.assertEqual([row['job']['id'] for row in original], [frontend, backend, history])
            self.assertTrue(all((row['score'], row['profile_score'], row['behavior_score'], row['final_score']) == (40, 40, 50, 43) for row in original))
            for action in ('save', 'applied'):
                self.assertEqual(client.post('/api/jobs/%s/interactions' % history, json={'type': action}).status_code, 201)
            ranked = client.get('/api/recommendations').json()
            self.assertEqual([row['job']['id'] for row in ranked], [backend, frontend])
            self.assertGreater(ranked[0]['behavior_score'], ranked[1]['behavior_score'])
            self.assertGreater(ranked[0]['final_score'], ranked[1]['final_score'])
            self.assertEqual(len(ranked[0]['behavior_reasons']), 2)
            for row in ranked:
                old = next(item for item in original if item['job']['id'] == row['job']['id'])
                self.assertEqual(profile_fit(row), profile_fit(old))
                self.assertEqual(row['score'], row['profile_score'])
            for _ in range(3):
                for action in ('save', 'applied'):
                    client.post('/api/jobs/%s/interactions' % history, json={'type': action})
            self.assertEqual(client.get('/api/recommendations').json(), ranked)
            self.assertEqual([row['job']['id'] for row in client.get('/api/feed').json()], [frontend, backend])
            for path in ('/api/saved', '/api/applied'):
                self.assertEqual(client.get(path).json()[0]['job']['id'], history)
                self.assertNotIn('final_score', client.get(path).json()[0])
            other = self.conn.execute('INSERT INTO candidate_profiles DEFAULT VALUES RETURNING id').fetchone()['id']
            db.record_interaction(self.conn, other, frontend, 'applied')
            db.record_interaction(self.conn, candidate, backend, 'view')
            # View only changes the state timestamp/cursor, never ranking signals.
            after_view = client.get('/api/recommendations').json()
            self.assertEqual([{key: value for key, value in row.items() if key != 'interaction_state'} for row in after_view],
                             [{key: value for key, value in row.items() if key != 'interaction_state'} for row in ranked])
            client.post('/api/jobs/%s/interactions' % backend, json={'type': 'dismiss'})
            self.assertEqual([row['job']['id'] for row in client.get('/api/recommendations').json()], [frontend])
        # Fresh connections rebuild preferences, while excluded jobs stay excluded.
        with patch('internshipfeed.api.db.connect', side_effect=self.connect), TestClient(app) as client:
            self.assertEqual([row['job']['id'] for row in client.get('/api/recommendations').json()], [frontend])
        self.assertEqual(self.conn.execute('SELECT * FROM canonical_jobs ORDER BY id').fetchall(), before_jobs)

    def test_behavior_history_keeps_unlinked_metadata_without_materializing_preferences(self):
        self.save(self.job)
        job_id = db.list_jobs(self.conn)[0].id
        candidate = db.development_profile(self.conn)['id']
        db.record_interaction(self.conn, candidate, job_id, 'applied')
        self.save(self.job.model_copy(update={'url': 'https://example.com/corrected-url'}))
        history = db.behavior_history(self.conn, candidate)
        self.assertEqual([entry.details.job.id for entry in history], [job_id])
        self.assertEqual(history[0].details.sources, [])
        self.assertEqual(db.behavior_history(self.conn, 999999), [])

    def test_recommendations_empty_dataset_and_min_score_validation(self):
        with patch('internshipfeed.api.db.connect', side_effect=self.connect), TestClient(app) as client:
            self.assertEqual(client.get('/api/recommendations').json(), [])
            for value in ('-1', '101', 'twenty'):
                with self.subTest(min_score=value):
                    self.assertEqual(client.get('/api/recommendations?min_score=' + value).status_code, 422)
            self.assertEqual(client.get('/api/recommendations?min_score=0').status_code, 200)

    def test_recommendations_missing_profile_fails_without_reseeding(self):
        self.conn.execute('DELETE FROM candidate_profiles')
        with patch('internshipfeed.api.db.connect', side_effect=self.connect), TestClient(app) as client:
            response = client.get('/api/recommendations')
        self.assertEqual(response.status_code, 503)
        self.assertIn('Development profile is missing', response.json()['detail'])
        self.assertEqual(self.conn.execute('SELECT count(*) AS n FROM candidate_profiles').fetchone()['n'], 0)

    def test_interaction_api_appends_events_for_the_development_candidate(self):
        self.save(self.job)
        job_id = db.list_jobs(self.conn)[0].id
        candidate = db.development_profile(self.conn)
        before_jobs = self.conn.execute('SELECT * FROM canonical_jobs ORDER BY id').fetchall()
        with patch('internshipfeed.api.db.connect', side_effect=self.connect), TestClient(app) as client:
            for action in ('save', 'save', 'click', 'dismiss', 'applied'):
                response = client.post('/api/jobs/%s/interactions' % job_id, json={'type': action})
                self.assertEqual(response.status_code, 201)
                event = response.json()
                self.assertEqual(event['canonical_job_id'], job_id)
                self.assertEqual(event['candidate_profile_id'], candidate['id'])
                self.assertEqual(event['interaction_type'], action)
                row = self.conn.execute('SELECT * FROM job_interactions WHERE id=%s', (event['id'],)).fetchone()
                self.assertEqual(row['created_at'], datetime.fromisoformat(event['created_at'].replace('Z', '+00:00')))
            states = client.get('/api/interactions').json()
        self.assertEqual(len(states), 1)
        self.assertEqual({key: states[0][key] for key in ('job_id', 'saved', 'dismissed', 'applied')},
                         {'job_id': job_id, 'saved': True, 'dismissed': True, 'applied': True})
        newest = self.conn.execute('SELECT max(created_at) AS latest FROM job_interactions').fetchone()['latest']
        self.assertEqual(datetime.fromisoformat(states[0]['last_interaction_at'].replace('Z', '+00:00')), newest)
        self.assertEqual(self.conn.execute('SELECT count(*) AS n FROM job_interactions').fetchone()['n'], 5)
        self.assertEqual(self.conn.execute('SELECT * FROM canonical_jobs ORDER BY id').fetchall(), before_jobs)
        self.assertEqual(db.development_profile(self.conn), candidate)

    def test_dismissed_collection_and_restore_preserve_other_state_and_history(self):
        self.save(self.job.model_copy(update={'title': 'Backend Engineer Intern'}))
        job_id = db.list_jobs(self.conn)[0].id
        before = self.conn.execute('SELECT * FROM canonical_jobs').fetchall()
        candidate = db.development_profile(self.conn)['id']
        with patch('internshipfeed.api.db.connect', side_effect=self.connect), TestClient(app) as client:
            self.assertEqual(client.get('/api/dismissed').json(), [])
            for action in ('save', 'applied', 'dismiss'):
                response = client.post('/api/jobs/%s/interactions' % job_id, json={'type': action})
                self.assertEqual(response.status_code, 201)
            rows = client.get('/api/dismissed').json()
            self.assertEqual([row['job']['id'] for row in rows], [job_id])
            self.assertIsNotNone(rows[0]['interaction_state']['dismissed_at'])
            self.assertEqual(client.get('/api/saved').json(), [])
            self.assertEqual(db.behavior_history(self.conn, candidate)[0].interaction_types, frozenset({'save', 'applied', 'dismiss'}))
            restored = client.post('/api/jobs/%s/interactions' % job_id, json={'type': 'undismiss'}).json()['interaction_state']
            self.assertFalse(restored['dismissed'])
            self.assertIsNone(restored['dismissed_at'])
            self.assertTrue(restored['saved'] and restored['applied'])
            self.assertEqual(client.get('/api/dismissed').json(), [])
            self.assertEqual(len(client.get('/api/saved').json()), 1)
            self.assertEqual(len(client.get('/api/applied').json()), 1)
            self.assertEqual(client.get('/api/recommendations').json(), [])
            self.assertEqual(db.behavior_history(self.conn, candidate)[0].interaction_types, frozenset({'save', 'applied'}))
            client.post('/api/jobs/%s/interactions' % job_id, json={'type': 'unapplied'})
            self.assertEqual(client.get('/api/recommendations').json()[0]['job']['id'], job_id)
        with patch('internshipfeed.api.db.connect', side_effect=self.connect), TestClient(app) as client:
            self.assertEqual(client.get('/api/dismissed').json(), [])
            self.assertFalse(client.get('/api/interactions').json()[0]['dismissed'])
        self.assertEqual([row['interaction_type'] for row in self.conn.execute('SELECT * FROM job_interactions ORDER BY id')], ['save', 'applied', 'dismiss', 'undismiss', 'unapplied'])
        self.assertEqual(self.conn.execute('SELECT * FROM canonical_jobs').fetchall(), before)

    def test_dismissed_order_uses_dismissal_time_and_retains_inactive_jobs(self):
        candidate = db.development_profile(self.conn)['id']
        ids = []
        for index, status in enumerate(('closed', 'dead', 'unlinked')):
            job = self.job.model_copy(update={'external_id': status, 'url': 'https://example.com/dismiss/' + status})
            self.save(job)
            job_id = db.get_source_job(self.conn, job.source, status).canonical_job_id
            ids.append(job_id)
            event = db.record_interaction(self.conn, candidate, job_id, 'dismiss')
            self.conn.execute("UPDATE job_interactions SET created_at=%s WHERE id=%s", (datetime(2026, 11, index + 1, tzinfo=timezone.utc), event['id']))
            db.record_interaction(self.conn, candidate, job_id, 'click')
            changed = job.model_copy(update={'url': 'https://example.com/dismiss/new'}) if status == 'unlinked' else job.model_copy(update={'url': None, 'status': status})
            self.save(changed)
        with patch('internshipfeed.api.db.connect', side_effect=self.connect), TestClient(app) as client:
            rows = client.get('/api/dismissed').json()
            self.assertEqual([row['job']['id'] for row in rows], list(reversed(ids)))
            self.assertEqual({row['job']['status'] for row in rows}, {'closed', 'dead', 'open'})
            for job_id in ids:
                client.post('/api/jobs/%s/interactions' % job_id, json={'type': 'undismiss'})
            self.assertEqual(client.get('/api/dismissed').json(), [])
            self.assertFalse(set(ids) & {row['job']['id'] for row in client.get('/api/recommendations').json()})

    def test_dismiss_reversal_follows_merges_and_is_candidate_scoped(self):
        candidate = db.development_profile(self.conn)['id']
        other = self.conn.execute('INSERT INTO candidate_profiles DEFAULT VALUES RETURNING id').fetchone()['id']
        records = [self.job.model_copy(update={'external_id': str(index), 'url': 'https://example.com/dismiss-merge/' + str(index)}) for index in range(2)]
        for record in records:
            self.save(record)
        a, b = [db.get_source_job(self.conn, record.source, record.external_id).canonical_job_id for record in records]
        db.record_interaction(self.conn, candidate, b, 'dismiss')
        db.record_interaction(self.conn, other, a, 'dismiss')
        self.save(records[1].model_copy(update={'resolved_url': records[0].url, 'description': 'Verified role', 'enrichment_status': 'success'}))
        with patch('internshipfeed.api.db.connect', side_effect=self.connect), TestClient(app) as client:
            self.assertEqual([row['job']['id'] for row in client.get('/api/dismissed').json()], [a])
            response = client.post('/api/jobs/%s/interactions' % b, json={'type': 'undismiss'})
            self.assertEqual(response.json()['canonical_job_id'], a)
            self.assertEqual(client.get('/api/dismissed').json(), [])
        self.assertEqual(db.behavior_history(self.conn, candidate), [])
        self.assertTrue(db.interaction_states(self.conn, other)[0]['dismissed'])

    def test_dismissal_migration_preserves_old_events(self):
        self.save(self.job)
        job_id = db.list_jobs(self.conn)[0].id
        candidate = db.development_profile(self.conn)['id']
        old = db.record_interaction(self.conn, candidate, job_id, 'dismiss')
        self.conn.execute('ALTER TABLE job_interactions DROP CONSTRAINT job_interactions_interaction_type_check')
        self.conn.execute("ALTER TABLE job_interactions ADD CONSTRAINT job_interactions_interaction_type_check CHECK (interaction_type IN ('view','click','save','unsave','dismiss','applied','unapplied'))")
        self.conn.execute("DELETE FROM schema_migrations WHERE version='011_reversible_dismissals.sql'")
        db.initialize(self.conn)
        db.initialize(self.conn)
        self.assertEqual(self.conn.execute('SELECT * FROM job_interactions WHERE id=%s', (old['id'],)).fetchone(), old)
        db.record_interaction(self.conn, candidate, job_id, 'undismiss')
        self.assertFalse(db.interaction_states(self.conn, candidate)[0]['dismissed'])

    def test_save_and_applied_reversals_restore_collections_and_survive_refresh(self):
        self.save(self.job)
        job_id = db.list_jobs(self.conn)[0].id
        before = self.conn.execute('SELECT * FROM canonical_jobs').fetchall()
        sequence = [('save', True, False), ('applied', True, True), ('unsave', False, True),
                    ('unapplied', False, False), ('save', True, False), ('applied', True, True),
                    ('unapplied', True, False), ('unsave', False, False), ('unsave', False, False), ('unapplied', False, False)]
        with patch('internshipfeed.api.db.connect', side_effect=self.connect), TestClient(app) as client:
            for action, saved, applied in sequence:
                response = client.post('/api/jobs/%s/interactions' % job_id, json={'type': action})
                self.assertEqual(response.status_code, 201)
                state = response.json()['interaction_state']
                self.assertEqual((state['saved'], state['applied']), (saved, applied))
                self.assertEqual(state['saved_at'] is not None, saved)
                self.assertEqual(state['applied_at'] is not None, applied)
                self.assertEqual(len(client.get('/api/saved').json()), int(saved))
                self.assertEqual(len(client.get('/api/applied').json()), int(applied))
                self.assertEqual(len(client.get('/api/recommendations').json()), int(not applied))
        with patch('internshipfeed.api.db.connect', side_effect=self.connect), TestClient(app) as client:
            state = client.get('/api/interactions').json()[0]
            self.assertFalse(state['saved'] or state['applied'])
            self.assertEqual(client.get('/api/saved').json(), [])
            self.assertEqual(client.get('/api/applied').json(), [])
            self.assertEqual(client.get('/api/recommendations').json()[0]['job']['id'], job_id)
        self.assertEqual([row['interaction_type'] for row in self.conn.execute('SELECT * FROM job_interactions ORDER BY id')], [item[0] for item in sequence])
        self.assertEqual(self.conn.execute('SELECT * FROM canonical_jobs').fetchall(), before)

    def test_reversals_remove_behavior_signals_without_repeated_toggle_inflation(self):
        from internshipfeed.behavioral import build_behavior_profile
        self.save(self.job.model_copy(update={'title': 'Backend Engineer Intern'}))
        job_id = db.list_jobs(self.conn)[0].id
        candidate = db.development_profile(self.conn)['id']
        for action, expected in (('click', 1), ('save', 3), ('applied', 7), ('unsave', 5), ('unapplied', 1),
                                 ('save', 3), ('applied', 7), ('save', 7)):
            db.record_interaction(self.conn, candidate, job_id, action)
            learned = build_behavior_profile(db.behavior_history(self.conn, candidate))
            self.assertEqual(learned.roles['backend engineering'], expected)
        self.assertEqual(self.conn.execute('SELECT count(*) AS n FROM job_interactions').fetchone()['n'], 8)

    def test_reversals_follow_merged_identities_and_remain_candidate_scoped(self):
        records = [self.job.model_copy(update={'external_id': str(index), 'url': 'https://example.com/reversal/' + str(index)}) for index in range(2)]
        for record in records:
            self.save(record)
        a, b = [db.get_source_job(self.conn, record.source, record.external_id).canonical_job_id for record in records]
        candidate = db.development_profile(self.conn)['id']
        other = self.conn.execute('INSERT INTO candidate_profiles DEFAULT VALUES RETURNING id').fetchone()['id']
        old = db.record_interaction(self.conn, candidate, b, 'save')
        db.record_interaction(self.conn, candidate, a, 'applied')
        db.record_interaction(self.conn, candidate, a, 'unsave')
        db.record_interaction(self.conn, candidate, b, 'unapplied')
        db.record_interaction(self.conn, other, a, 'save')
        self.save(records[1].model_copy(update={'resolved_url': records[0].url, 'description': 'Verified posting', 'enrichment_status': 'success'}))
        state = db.interaction_states(self.conn, candidate)[0]
        self.assertEqual(state['job_id'], a)
        self.assertFalse(state['saved'] or state['applied'])
        self.assertEqual(db.behavior_history(self.conn, candidate), [])
        self.assertTrue(db.interaction_states(self.conn, other)[0]['saved'])
        with patch('internshipfeed.api.db.connect', side_effect=self.connect), TestClient(app) as client:
            response = client.post('/api/jobs/%s/interactions' % b, json={'type': 'save'})
            self.assertEqual(response.json()['canonical_job_id'], a)
            self.assertTrue(response.json()['interaction_state']['saved'])
        self.assertEqual(self.conn.execute('SELECT canonical_job_id FROM job_interactions WHERE id=%s', (old['id'],)).fetchone()['canonical_job_id'], b)

    def test_latest_event_id_decides_state_even_when_timestamps_tie_or_move_backwards(self):
        self.save(self.job)
        job_id = db.list_jobs(self.conn)[0].id
        candidate = db.development_profile(self.conn)['id']
        first = db.record_interaction(self.conn, candidate, job_id, 'save')
        self.conn.execute("UPDATE job_interactions SET created_at='2026-11-10T12:00:00Z' WHERE id=%s", (first['id'],))
        undone = db.record_interaction(self.conn, candidate, job_id, 'unsave')
        self.conn.execute("UPDATE job_interactions SET created_at='2026-11-01T12:00:00Z' WHERE id=%s", (undone['id'],))
        state = db.interaction_states(self.conn, candidate)[0]
        self.assertFalse(state['saved'])
        self.assertIsNone(state['saved_at'])
        restored = db.record_interaction(self.conn, candidate, job_id, 'save')
        self.conn.execute("UPDATE job_interactions SET created_at='2026-11-01T12:00:00Z' WHERE id=%s", (restored['id'],))
        state = db.interaction_states(self.conn, candidate)[0]
        self.assertTrue(state['saved'])
        self.assertEqual(state['saved_at'].day, 1)
        self.assertEqual(state['last_interaction_at'].day, 10)
        self.assertEqual(state['last_event_id'], restored['id'])

    def test_reversible_interaction_migration_preserves_existing_history_and_constraints(self):
        self.save(self.job)
        job_id = db.list_jobs(self.conn)[0].id
        candidate = db.development_profile(self.conn)['id']
        event = db.record_interaction(self.conn, candidate, job_id, 'save')
        self.conn.execute('ALTER TABLE job_interactions DROP CONSTRAINT job_interactions_interaction_type_check')
        self.conn.execute("ALTER TABLE job_interactions ADD CONSTRAINT job_interactions_interaction_type_check CHECK (interaction_type IN ('view','click','save','dismiss','applied'))")
        self.conn.execute("DELETE FROM schema_migrations WHERE version='008_reversible_interactions.sql'")
        db.initialize(self.conn)
        db.initialize(self.conn)
        self.assertEqual(self.conn.execute('SELECT * FROM job_interactions WHERE id=%s', (event['id'],)).fetchone(), event)
        db.record_interaction(self.conn, candidate, job_id, 'unsave')
        self.assertFalse(db.interaction_states(self.conn, candidate)[0]['saved'])
        with self.assertRaises(psycopg.errors.CheckViolation):
            db.record_interaction(self.conn, candidate, job_id, 'invalid')

    def test_click_and_internal_view_do_not_imply_saved_or_applied(self):
        self.save(self.job)
        job_id = db.list_jobs(self.conn)[0].id
        with patch('internshipfeed.api.db.connect', side_effect=self.connect), TestClient(app) as client:
            self.assertEqual(client.get('/api/interactions').json(), [])
            response = client.post('/api/jobs/%s/interactions' % job_id, json={'type': 'click'})
            self.assertEqual(response.status_code, 201)
            state = client.get('/api/interactions').json()[0]
        self.assertFalse(state['saved'] or state['dismissed'] or state['applied'])
        db.record_interaction(self.conn, db.development_profile(self.conn)['id'], job_id, 'view')
        after = db.interaction_states(self.conn, db.development_profile(self.conn)['id'])[0]
        self.assertFalse(after['saved'] or after['dismissed'] or after['applied'])

    def test_interaction_validation_missing_jobs_and_local_mutation_policy(self):
        self.save(self.job)
        job_id = db.list_jobs(self.conn)[0].id
        orphan = self.conn.execute('INSERT INTO canonical_job_identities DEFAULT VALUES RETURNING id').fetchone()['id']
        with patch('internshipfeed.api.db.connect', side_effect=self.connect), TestClient(app) as client:
            for body in ({'type': 'favorite'}, {'type': 'view'}, {'type': 123}, {},
                         {'type': 'save', 'candidate_profile_id': 999}, {'type': 'save', 'extra': True}):
                with self.subTest(body=body):
                    self.assertEqual(client.post('/api/jobs/%s/interactions' % job_id, json=body).status_code, 422)
            for missing in (999999, orphan):
                self.assertEqual(client.post('/api/jobs/%s/interactions' % missing, json={'type': 'save'}).status_code, 404)
            self.assertEqual(client.post('/api/jobs/%s/interactions' % job_id, json={'type': 'save'},
                                         headers={'Origin': 'https://other.example'}).status_code, 403)
        self.assertEqual(self.conn.execute('SELECT count(*) AS n FROM job_interactions').fetchone()['n'], 0)

    def test_interaction_states_are_candidate_specific(self):
        self.save(self.job)
        job_id = db.list_jobs(self.conn)[0].id
        first = db.development_profile(self.conn)['id']
        other = self.conn.execute('INSERT INTO candidate_profiles DEFAULT VALUES RETURNING id').fetchone()['id']
        db.record_interaction(self.conn, other, job_id, 'dismiss')
        db.record_interaction(self.conn, other, job_id, 'applied')
        with patch('internshipfeed.api.db.connect', side_effect=self.connect), TestClient(app) as client:
            self.assertEqual(client.get('/api/interactions').json(), [])
            response = client.post('/api/jobs/%s/interactions?candidate_profile_id=%s' % (job_id, other), json={'type': 'save'})
            self.assertEqual(response.json()['candidate_profile_id'], first)
            states = client.get('/api/interactions').json()
        self.assertEqual(len(states), 1)
        self.assertTrue(states[0]['saved'])
        self.assertFalse(states[0]['dismissed'] or states[0]['applied'])
        self.assertTrue(db.interaction_states(self.conn, other)[0]['dismissed'])

    def test_interactions_follow_multi_hop_merges_without_rewriting_history(self):
        records = []
        for name in ('a', 'b', 'c'):
            record = self.job.model_copy(update={'external_id': name, 'url': 'https://example.com/jobs/' + name})
            self.save(record)
            records.append(record)
        ids = [db.get_source_job(self.conn, record.source, record.external_id).canonical_job_id for record in records]
        candidate = db.development_profile(self.conn)['id']
        old = db.record_interaction(self.conn, candidate, ids[2], 'save')
        db.record_interaction(self.conn, candidate, ids[0], 'applied')
        self.save(records[2].model_copy(update={'description': 'Readable job', 'enrichment_status': 'success', 'resolved_url': records[1].url}))
        self.save(records[1].model_copy(update={'description': 'Readable job', 'enrichment_status': 'success', 'resolved_url': records[0].url}))
        with patch('internshipfeed.api.db.connect', side_effect=self.connect), TestClient(app) as client:
            response = client.post('/api/jobs/%s/interactions' % ids[2], json={'type': 'click'})
            self.assertEqual(response.status_code, 201)
            self.assertEqual(response.json()['canonical_job_id'], ids[0])
            states = client.get('/api/interactions').json()
        self.assertEqual(len(states), 1)
        self.assertEqual(states[0]['job_id'], ids[0])
        self.assertTrue(states[0]['saved'] and states[0]['applied'])
        self.assertFalse(states[0]['dismissed'])
        self.assertEqual(self.conn.execute('SELECT canonical_job_id FROM job_interactions WHERE id=%s', (old['id'],)).fetchone()['canonical_job_id'], ids[2])

    def test_interaction_apis_missing_profile_fail_without_reseeding(self):
        self.save(self.job)
        job_id = db.list_jobs(self.conn)[0].id
        self.conn.execute('DELETE FROM candidate_profiles')
        with patch('internshipfeed.api.db.connect', side_effect=self.connect), TestClient(app) as client:
            for response in (client.get('/api/interactions'), client.post('/api/jobs/%s/interactions' % job_id, json={'type': 'save'})):
                self.assertEqual(response.status_code, 503)
                self.assertIn('Development profile is missing', response.json()['detail'])
        self.assertEqual(self.conn.execute('SELECT count(*) AS n FROM candidate_profiles').fetchone()['n'], 0)

    def test_interaction_endpoints_do_not_change_fit_scores_or_canonical_jobs(self):
        self.save(self.job)
        job_id = db.list_jobs(self.conn)[0].id
        before_jobs = self.conn.execute('SELECT * FROM canonical_jobs').fetchall()
        with patch('internshipfeed.api.db.connect', side_effect=self.connect), TestClient(app) as client:
            before = client.get('/api/recommendations').json()
            for action in ('save', 'applied', 'dismiss', 'click'):
                self.assertEqual(client.post('/api/jobs/%s/interactions' % job_id, json={'type': action}).status_code, 201)
            self.assertEqual(client.get('/api/recommendations').json(), [])
            applied = client.get('/api/applied').json()
            self.assertEqual([profile_fit(row) for row in applied], [profile_fit(row) for row in before])
            self.assertEqual(client.get('/api/saved').json(), [])
            self.assertEqual(client.get('/api/jobs').json(), [])
            self.assertEqual(client.get('/api/feed').json(), [])
            self.assertTrue(client.get('/api/interactions').json()[0]['dismissed'])
        self.assertEqual(self.conn.execute('SELECT * FROM canonical_jobs').fetchall(), before_jobs)

    def test_workflow_views_follow_save_dismiss_applied_and_click_history(self):
        ids = []
        for name in ('a', 'b', 'c'):
            job = self.job.model_copy(update={'external_id': name, 'url': 'https://example.com/job/' + name,
                                              'title': 'Backend Engineer Intern', 'term': 'Winter 2027'})
            self.save(job)
            ids.append(db.get_source_job(self.conn, job.source, name).canonical_job_id)
        a, b, c = ids
        with patch('internshipfeed.api.db.connect', side_effect=self.connect), TestClient(app) as client:
            client.put('/api/profile', json={'target_roles': ['Backend Engineering'], 'skills': [], 'preferred_locations': ['Toronto'],
                                            'preferred_terms': ['Winter 2027'], 'graduation_year': None})
            before = {row['job']['id']: row for row in client.get('/api/recommendations').json()}
            def members(path):
                response = client.get(path)
                self.assertEqual(response.status_code, 200)
                return {row['job']['id']: row for row in response.json()}
            def act(job_id, action):
                response = client.post('/api/jobs/%s/interactions' % job_id, json={'type': action})
                self.assertEqual(response.status_code, 201)
                return response.json()
            click = act(b, 'click')
            self.assertFalse(click['interaction_state']['applied'])
            self.assertEqual(set(members('/api/recommendations')), set(ids))
            save = act(a, 'save')
            self.assertTrue(save['interaction_state']['saved'])
            self.assertEqual(save['interaction_state']['saved_at'], save['created_at'])
            self.assertEqual(set(members('/api/recommendations')), set(ids))
            self.assertEqual(set(members('/api/saved')), {a})
            self.assertEqual(members('/api/applied'), {})
            act(c, 'applied')
            self.assertEqual(set(members('/api/recommendations')), {a, b})
            self.assertEqual(set(members('/api/applied')), {c})
            act(c, 'save')
            self.assertEqual(set(members('/api/saved')), {a, c})
            self.assertTrue(members('/api/saved')[c]['interaction_state']['applied'])
            act(b, 'dismiss')
            self.assertEqual(set(members('/api/recommendations')), {a})
            self.assertEqual({row['id'] for row in client.get('/api/jobs').json()}, {a})
            self.assertEqual({row['job']['id'] for row in client.get('/api/feed').json()}, {a})
            for row in list(members('/api/saved').values()) + list(members('/api/applied').values()):
                for field in ('score', 'role_score', 'skills_score', 'location_score', 'term_score', 'reasons', 'matched_skills'):
                    self.assertEqual(row[field], before[row['job']['id']][field])
            act(a, 'dismiss')
            self.assertEqual(members('/api/recommendations'), {})
            self.assertEqual(set(members('/api/saved')), {c})
            act(c, 'dismiss')
            self.assertEqual(members('/api/saved'), {})
            self.assertEqual(set(members('/api/applied')), {c})
        # A new client/connection reconstructs all views from persistent history.
        with patch('internshipfeed.api.db.connect', side_effect=self.connect), TestClient(app) as client:
            self.assertEqual(client.get('/api/recommendations').json(), [])
            self.assertEqual(client.get('/api/saved').json(), [])
            self.assertEqual([row['job']['id'] for row in client.get('/api/applied').json()], [c])
        self.assertEqual(len(db.list_jobs(self.conn)), 3)  # Product filtering never deletes canonical jobs.

    def test_applied_order_uses_latest_applied_event_not_later_clicks(self):
        ids = []
        for name in ('a', 'b'):
            job = self.job.model_copy(update={'external_id': name, 'url': 'https://example.com/job/' + name})
            self.save(job)
            ids.append(db.get_source_job(self.conn, job.source, name).canonical_job_id)
        candidate = db.development_profile(self.conn)['id']
        for job_id, action, timestamp in ((ids[0], 'applied', '2026-11-01T12:00:00Z'),
                                           (ids[1], 'applied', '2026-11-02T12:00:00Z'),
                                           (ids[0], 'click', '2026-11-03T12:00:00Z')):
            event = db.record_interaction(self.conn, candidate, job_id, action)
            self.conn.execute('UPDATE job_interactions SET created_at=%s WHERE id=%s', (timestamp, event['id']))
        with patch('internshipfeed.api.db.connect', side_effect=self.connect), TestClient(app) as client:
            rows = client.get('/api/applied').json()
            self.assertEqual([row['job']['id'] for row in rows], list(reversed(ids)))
            state = rows[1]['interaction_state']
            self.assertEqual(datetime.fromisoformat(state['applied_at'].replace('Z', '+00:00')).day, 1)
            self.assertEqual(datetime.fromisoformat(state['last_interaction_at'].replace('Z', '+00:00')).day, 3)
            repeated = db.record_interaction(self.conn, candidate, ids[0], 'applied')
            self.conn.execute("UPDATE job_interactions SET created_at='2026-11-04T12:00:00Z' WHERE id=%s", (repeated['id'],))
            self.assertEqual([row['job']['id'] for row in client.get('/api/applied').json()], ids)

    def test_saved_and_applied_retain_closed_dead_and_unlinked_jobs(self):
        ids = []
        candidate = db.development_profile(self.conn)['id']
        for status in ('closed', 'dead', 'unlinked'):
            job = self.job.model_copy(update={'external_id': status, 'url': 'https://example.com/job/' + status})
            self.save(job)
            job_id = db.get_source_job(self.conn, job.source, status).canonical_job_id
            ids.append(job_id)
            db.record_interaction(self.conn, candidate, job_id, 'save')
            db.record_interaction(self.conn, candidate, job_id, 'applied')
            changed = job.model_copy(update={'url': 'https://example.com/job/replacement'}) if status == 'unlinked' else job.model_copy(update={'status': status, 'url': None})
            self.save(changed)
        with patch('internshipfeed.api.db.connect', side_effect=self.connect), TestClient(app) as client:
            for path in ('/api/saved', '/api/applied'):
                rows = client.get(path).json()
                self.assertEqual({row['job']['id'] for row in rows}, set(ids))
                self.assertEqual({row['job']['status'] for row in rows}, {'closed', 'dead', 'open'})
            self.assertFalse(set(ids) & {row['job']['id'] for row in client.get('/api/recommendations').json()})

    def test_workflow_views_scope_candidate_and_follow_merge_history(self):
        records = [self.job.model_copy(update={'external_id': str(index), 'url': 'https://example.com/job/' + str(index)}) for index in range(2)]
        for record in records:
            self.save(record)
        ids = [db.get_source_job(self.conn, record.source, record.external_id).canonical_job_id for record in records]
        other = self.conn.execute('INSERT INTO candidate_profiles DEFAULT VALUES RETURNING id').fetchone()['id']
        db.record_interaction(self.conn, other, ids[0], 'dismiss')
        db.record_interaction(self.conn, other, ids[0], 'applied')
        with patch('internshipfeed.api.db.connect', side_effect=self.connect), TestClient(app) as client:
            self.assertEqual(len(client.get('/api/recommendations').json()), 2)
            self.assertEqual(client.get('/api/saved').json(), [])
            self.assertEqual(client.get('/api/applied').json(), [])
            client.post('/api/jobs/%s/interactions' % ids[1], json={'type': 'save'})
            client.post('/api/jobs/%s/interactions' % ids[1], json={'type': 'applied'})
            self.save(records[1].model_copy(update={'description': 'Readable job', 'enrichment_status': 'success', 'resolved_url': records[0].url}))
            for path in ('/api/saved', '/api/applied'):
                rows = client.get(path).json()
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]['job']['id'], ids[0])
                self.assertTrue(rows[0]['interaction_state']['saved'] and rows[0]['interaction_state']['applied'])
            self.assertEqual(client.get('/api/recommendations').json(), [])

    def test_workflow_views_fail_clearly_without_a_profile(self):
        self.conn.execute('DELETE FROM candidate_profiles')
        with patch('internshipfeed.api.db.connect', side_effect=self.connect), TestClient(app) as client:
            for path in ('/api/saved', '/api/applied', '/api/dismissed', '/api/recommendations', '/api/feed', '/api/jobs'):
                self.assertEqual(client.get(path).status_code, 503)

    def test_pdf_resume_upload_persists_text_metadata_and_preserves_profile(self):
        before = db.development_profile(self.conn)
        content = make_pdf(['Sample Candidate\nEducation', None, 'Projects\nPython / SQL / C++'])
        with patch('internshipfeed.api.db.connect', side_effect=self.connect), TestClient(app) as client:
            response = client.post('/api/resumes', files={'file': ('resume.pdf', content, 'application/pdf')})
        self.assertEqual(response.status_code, 201)
        resume = response.json()
        self.assertEqual(set(resume), {'id', 'candidate_profile_id', 'filename', 'extracted_text', 'uploaded_at'})
        self.assertEqual(resume['candidate_profile_id'], before['id'])
        self.assertEqual(resume['filename'], 'resume.pdf')
        self.assertEqual(resume['extracted_text'], 'Sample Candidate\nEducation\n\nProjects\nPython / SQL / C++')
        row = self.conn.execute('SELECT * FROM resumes WHERE id=%s', (resume['id'],)).fetchone()
        self.assertEqual(set(row), set(resume))
        self.assertEqual(row['extracted_text'], resume['extracted_text'])
        self.assertEqual(row['candidate_profile_id'], before['id'])
        self.assertEqual(row['uploaded_at'], datetime.fromisoformat(resume['uploaded_at'].replace('Z', '+00:00')))
        self.assertEqual(db.development_profile(self.conn), before)

    def test_resume_history_uppercase_extension_and_candidate_association(self):
        profile = db.development_profile(self.conn)
        with patch('internshipfeed.api.db.connect', side_effect=self.connect), TestClient(app) as client:
            first = client.post('/api/resumes', files={'file': ('resume_v1.pdf', make_pdf(['Version one']), 'application/pdf')})
            second = client.post('/api/resumes', data={'candidate_profile_id': '999999'},
                                 files={'file': ('RESUME.PDF', make_pdf(['Version two']), 'application/octet-stream')})
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 201)
        self.assertNotEqual(first.json()['id'], second.json()['id'])
        rows = self.conn.execute('SELECT * FROM resumes ORDER BY id').fetchall()
        self.assertEqual(len(rows), 2)
        self.assertEqual([row['candidate_profile_id'] for row in rows], [profile['id'], profile['id']])
        self.assertEqual([row['extracted_text'] for row in rows], ['Version one', 'Version two'])
        self.assertEqual([row['filename'] for row in rows], ['resume_v1.pdf', 'RESUME.PDF'])

    def test_resume_rejects_other_extensions_and_incompatible_mime(self):
        content = make_pdf(['Sample Candidate'])
        cases = [('resume.' + extension, 'application/pdf') for extension in
                 ('doc', 'docx', 'txt', 'png', 'jpg', 'jpeg', 'rtf', 'pages')]
        cases.extend([('resume.pdf', 'text/plain'), ('resume.pdf', 'image/png')])
        with patch('internshipfeed.api.db.connect', side_effect=self.connect), TestClient(app) as client:
            for filename, mime in cases:
                with self.subTest(filename=filename, mime=mime), patch('internshipfeed.api.extract_pdf_text') as extract:
                    response = client.post('/api/resumes', files={'file': (filename, content, mime)})
                    self.assertEqual(response.status_code, 415)
                    extract.assert_not_called()
        self.assertEqual(self.conn.execute('SELECT count(*) AS n FROM resumes').fetchone()['n'], 0)

    def test_resume_rejects_corrupt_empty_and_too_many_pages_without_inserting(self):
        cases = [(b'private content not a PDF', 'Could not read this PDF'),
                 (b'%PDF-1.4\ncorrupt', 'Could not read this PDF'),
                 (make_pdf([None]), 'Could not extract readable text'),
                 (make_pdf(['page'] * (MAX_PDF_PAGES + 1)), 'at most 10 pages')]
        with patch('internshipfeed.api.db.connect', side_effect=self.connect), TestClient(app) as client:
            for content, message in cases:
                with self.subTest(message=message):
                    response = client.post('/api/resumes', files={'file': ('resume.pdf', content, 'application/pdf')})
                    self.assertEqual(response.status_code, 422)
                    self.assertIn(message, response.json()['detail'])
                    self.assertNotIn('private content', response.text)
                    self.assertEqual(self.conn.execute('SELECT count(*) AS n FROM resumes').fetchone()['n'], 0)

    def test_resume_size_limit_prevents_extraction_and_persistence(self):
        with patch('internshipfeed.api.db.connect', side_effect=self.connect), TestClient(app) as client, patch('internshipfeed.api.extract_pdf_text') as extract:
            response = client.post('/api/resumes', files={'file': ('resume.pdf', b'x' * (MAX_UPLOAD_BYTES + 1), 'application/pdf')})
        self.assertEqual(response.status_code, 413)
        extract.assert_not_called()
        self.assertEqual(self.conn.execute('SELECT count(*) AS n FROM resumes').fetchone()['n'], 0)

    def test_resume_missing_file_profile_and_cross_origin_fail_without_insert(self):
        content = make_pdf(['Sample Candidate'])
        with patch('internshipfeed.api.db.connect', side_effect=self.connect), TestClient(app) as client:
            self.assertEqual(client.post('/api/resumes').status_code, 422)
            rejected = client.post('/api/resumes', files={'file': ('resume.pdf', content, 'application/pdf')},
                                   headers={'Origin': 'https://evil.example'})
            self.assertEqual(rejected.status_code, 403)
            self.conn.execute('DELETE FROM candidate_profiles')
            with patch('internshipfeed.api.extract_pdf_text') as extract:
                missing = client.post('/api/resumes', files={'file': ('resume.pdf', content, 'application/pdf')})
                self.assertEqual(missing.status_code, 503)
                self.assertIn('Development profile is missing', missing.json()['detail'])
                extract.assert_not_called()
        self.assertEqual(self.conn.execute('SELECT count(*) AS n FROM resumes').fetchone()['n'], 0)

    def test_resume_stores_only_basename_from_client_path(self):
        with patch('internshipfeed.api.db.connect', side_effect=self.connect), TestClient(app) as client:
            response = client.post('/api/resumes', files={'file': ('/private/client/resume.Pdf', make_pdf(['Sample Candidate']), 'application/pdf')})
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()['filename'], 'resume.Pdf')
        self.assertEqual(self.conn.execute('SELECT filename FROM resumes').fetchone()['filename'], 'resume.Pdf')

    def test_identical_snapshots_deduplicate_per_source(self):
        source = db.ensure_source(self.conn, self.job.source)
        first = db.save_snapshot(self.conn, source['id'], 'same upstream content', commit_sha='a' * 40)
        again = db.save_snapshot(self.conn, source['id'], 'same upstream content')
        self.assertEqual(first['id'], again['id'])
        self.assertEqual(first['fetched_at'], again['fetched_at'])
        self.assertEqual(again['raw_content'], 'same upstream content')
        self.assertEqual(again['commit_sha'], 'a' * 40)
        other = db.ensure_source(self.conn, 'another-source')
        separate = db.save_snapshot(self.conn, other['id'], 'same upstream content')
        self.assertNotEqual(first['id'], separate['id'])
        self.assertEqual(first['content_hash'], separate['content_hash'])

    def test_snapshot_must_belong_to_posting_source(self):
        other = db.ensure_source(self.conn, 'another-source')
        bad = self.job.model_copy(update={'source': other['repository'], 'source_id': other['id']})
        with self.assertRaisesRegex(ValueError, 'snapshot must belong'):
            db.save_job(self.conn, bad)
        with self.assertRaises(psycopg.errors.ForeignKeyViolation):
            self.conn.execute("INSERT INTO source_postings (source_id, snapshot_id, external_id, company, title, location, status, source_line) VALUES (%s,%s,'bad','Acme','Intern','Toronto','open',1)", (other['id'], int(self.job.snapshot_id)))

    def test_two_source_runs_store_lineage_raw_fields_and_counters(self):
        from internshipfeed.ingestion import run_ingestion
        original_connect = db.connect
        with tempfile.TemporaryDirectory() as directory, patch('internshipfeed.ingestion.db.connect', side_effect=lambda: original_connect(self.dsn)):
            result = run_ingestion(input_paths={'canadian-tech': FIXTURE, 'simplify': FIXTURE.parent / 'simplify.md'}, output=directory, enrich=False)
        runs = db.list_ingestion_runs(self.conn)
        self.assertEqual(len(runs), 2)
        self.assertEqual({str(run['batch_id']) for run in runs}, {result['id']})
        self.assertTrue(all(run['records_fetched'] == run['records_parsed'] == run['records_inserted'] == 3 for run in runs))
        self.assertTrue(all(run['snapshot_id'] and run['finished_at'] and run['status'] == 'success' for run in runs))
        snapshot = self.conn.execute('SELECT * FROM source_snapshots WHERE id=%s', (runs[0]['snapshot_id'],)).fetchone()
        self.assertTrue(snapshot['raw_content'])
        row = self.conn.execute("SELECT * FROM source_postings WHERE raw_company='↳' LIMIT 1").fetchone()
        self.assertEqual(row['company'], 'Acme')
        self.assertEqual(row['status'], 'closed')
        self.assertEqual(self.conn.execute('SELECT count(*) AS n FROM canonical_job_sources').fetchone()['n'], 6)
        mapping = self.conn.execute("SELECT * FROM canonical_job_sources WHERE match_method='exact_application_url' LIMIT 1").fetchone()
        self.assertEqual(float(mapping['match_confidence']), 1)
        self.assertTrue(mapping['matched_url'])

    def test_disabled_source_is_not_ingested(self):
        from internshipfeed.ingestion import run_ingestion
        original_connect = db.connect
        self.conn.execute("UPDATE job_sources SET enabled=false WHERE key='canadian-tech'")
        with tempfile.TemporaryDirectory() as directory, patch('internshipfeed.ingestion.db.connect', side_effect=lambda: original_connect(self.dsn)):
            run = run_ingestion(input_paths={'canadian-tech': FIXTURE, 'simplify': FIXTURE.parent / 'simplify.md'}, output=directory, enrich=False)
        self.assertEqual([item['key'] for item in run['sources']], ['simplify'])
        self.assertEqual(run['inserted'], 3)

    def test_parse_failure_keeps_snapshot_and_failed_source_run(self):
        from internshipfeed.ingestion import run_ingestion
        original_connect = db.connect
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'broken.md'
            path.write_text('Upstream format changed')
            with patch('internshipfeed.ingestion.db.connect', side_effect=lambda: original_connect(self.dsn)):
                result = run_ingestion(input_path=path, output=directory, enrich=False)
        run = db.list_ingestion_runs(self.conn)[0]
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(run['status'], 'failed')
        self.assertIsNotNone(run['snapshot_id'])
        self.assertTrue(run['error_summary'])
        self.assertEqual(self.conn.execute('SELECT raw_content FROM source_snapshots WHERE id=%s', (run['snapshot_id'],)).fetchone()['raw_content'], 'Upstream format changed')

    def test_canonical_metadata_and_event_log_persist(self):
        self.job.description = 'Build tools with Python.'
        self.job.keywords = JobKeywords(languages=['Python'])
        self.job.enrichment_status = 'success'
        self.save(self.job)
        canonical_id = db.get_source_job(self.conn, self.job.source, self.job.external_id).canonical_job_id
        stored = self.conn.execute('SELECT * FROM canonical_jobs WHERE id=%s', (canonical_id,)).fetchone()
        self.assertEqual(stored['description'], self.job.description)
        self.assertEqual(stored['keywords']['languages'], ['Python'])
        self.assertIsNotNone(stored['first_seen_at'])
        self.assertIsNotNone(stored['created_at'])
        profile = db.development_profile(self.conn)
        first = db.record_interaction(self.conn, profile['id'], canonical_id, 'view')
        second = db.record_interaction(self.conn, profile['id'], canonical_id, 'view')
        self.assertNotEqual(first['id'], second['id'])
        for event in ('click', 'save', 'dismiss', 'applied'):
            db.record_interaction(self.conn, profile['id'], canonical_id, event)
        self.assertEqual(self.conn.execute('SELECT count(*) AS n FROM job_interactions').fetchone()['n'], 6)
        with self.assertRaises(psycopg.errors.CheckViolation):
            db.record_interaction(self.conn, profile['id'], canonical_id, 'overwrite')
        resume = db.save_resume_text(self.conn, profile['id'], 'resume.pdf', 'Extracted text')
        self.assertEqual(resume['extracted_text'], 'Extracted text')
        self.assertNotIn('raw_file', resume)


    def test_same_repository_datasets_keep_independent_source_identity(self):
        original = db.ensure_source(self.conn, self.job.source)
        other = self.conn.execute("INSERT INTO job_sources (key, name, provider_type, repository, file_path, branch, enabled) VALUES ('alternate', 'Alternate dataset', 'github', %s, 'archived/README.md', 'main', false) RETURNING *", (self.job.source,)).fetchone()
        snapshot = db.save_snapshot(self.conn, other['id'], FIXTURE.read_text())
        db.save_job(self.conn, self.job)
        alternate = self.job.model_copy(update={'source_id': other['id'], 'snapshot_id': str(snapshot['id'])})
        db.save_job(self.conn, alternate)
        first = db.get_source_job(self.conn, original['id'], self.job.external_id)
        second = db.get_source_job(self.conn, other['id'], self.job.external_id)
        self.assertNotEqual(first.id, second.id)
        self.assertEqual(first.canonical_job_id, second.canonical_job_id)
        self.assertEqual(len(db.list_jobs(self.conn, source_id=other['id'])), 1)
        with self.assertRaisesRegex(ValueError, 'multiple datasets'):
            db.get_source_job(self.conn, self.job.source, self.job.external_id)
