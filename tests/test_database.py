"""Real PostgreSQL tests; run with scripts/test_with_postgres.py or TEST_DATABASE_URL."""
import json
import os
import tempfile
import time
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import psycopg
from fastapi.testclient import TestClient
from psycopg import sql
from psycopg.conninfo import make_conninfo

from internshipfeed import db
from internshipfeed.api import app, connection
from internshipfeed.cli import main
from internshipfeed.sources import CANADIAN

SOURCE_URL = CANADIAN.url
from internshipfeed.models import JobKeywords
from internshipfeed.normalize import normalize
from internshipfeed.source import parse_readme

FIXTURE = Path(__file__).parent / "fixtures" / "listings.md"


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
        self.records = parse_readme(FIXTURE.read_text())
        self.job = normalize(self.records[0], SOURCE_URL, "a")

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
            self.conn.execute("INSERT INTO source_jobs (source, external_id, company, title, location, status, snapshot_id, source_line, canonical_job_id) VALUES (%s, %s, 'x', 'y', 'z', 'open', 'a', 1, %s)", (self.job.source, self.job.external_id, db.get_source_job(self.conn, self.job.source, self.job.external_id).canonical_job_id))

    def test_closed_update_preserves_description_url_and_first_seen(self):
        self.job.description = "Experience with Python and React."
        self.job.keywords = JobKeywords(languages=["Python"], frameworks=["React"], requirements=["Experience with Python and React."])
        self.job.enrichment_status = "success"
        self.job.enriched_at = datetime.now(timezone.utc)
        self.assertEqual(db.save_job(self.conn, self.job), "inserted")
        before = db.list_source_jobs(self.conn, include_inactive=True)[0]
        closed = normalize(dict(self.records[0], status="closed", raw_url=None), SOURCE_URL, "b")
        self.assertEqual(db.save_job(self.conn, closed), "updated")
        after = db.list_source_jobs(self.conn, include_inactive=True)[0]
        self.assertEqual(after.id, before.id)
        self.assertEqual(after.first_seen_at, before.first_seen_at)
        self.assertGreaterEqual(after.last_seen_at, before.last_seen_at)
        self.assertEqual(after.status, "closed")
        self.assertIsNone(after.url)
        self.assertEqual(after.last_known_url, before.url)
        self.assertEqual(after.description, before.description)
        self.assertEqual(after.keywords, before.keywords)
        self.assertEqual(db.save_job(self.conn, closed), "skipped_duplicates")
        # Failed refreshes must not erase previously extracted descriptions.
        closed.enrichment_status = "failed"
        closed.enrichment_error = "timeout"
        db.save_job(self.conn, closed)
        after = db.list_source_jobs(self.conn, include_inactive=True)[0]
        self.assertEqual(after.description, before.description)
        self.assertEqual(after.enrichment_status, "failed")

    def test_api_uses_database_and_exposes_details(self):
        self.job.description = "Requirements: Python and React"
        self.job.keywords = JobKeywords(frameworks=["React"])
        self.job.enrichment_status = "success"
        db.save_job(self.conn, self.job)
        def test_connection():
            with self.connect() as conn:
                yield conn
        app.dependency_overrides[connection] = test_connection
        with patch("internshipfeed.api.db.connect", side_effect=self.connect), TestClient(app) as client:
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
            self.assertEqual(db.latest_run(self.conn)["id"], second["id"])
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
            db.save_job(self.conn, normalize(record, SOURCE_URL, "snapshot"))
        active = db.list_jobs(self.conn)
        self.assertEqual(len(active), 2)
        closed = next(job for job in db.list_source_jobs(self.conn, include_inactive=True) if job.status == "closed")
        self.assertIsNone(db.get_job(self.conn, closed.canonical_job_id))
        self.job.status = "dead"
        db.save_job(self.conn, self.job)
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
                    report = client.get("/api/ingestion-runs/" + run_id).json()
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
        db.save_job(self.conn, first)
        db.save_job(self.conn, second)
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
        self.assertEqual(db.save_job(self.conn, second), 'skipped_duplicates')
        self.assertEqual(db.get_source_job(self.conn, second.source, second.external_id).canonical_job_id, a.canonical_job_id)

    def test_same_title_different_or_missing_urls_not_merged(self):
        first = self.job.model_copy(update={'url': 'https://stripe.com/jobs/search?gh_jid=123'})
        second = first.model_copy(update={'source': 'source-b', 'external_id': '875', 'url': 'https://stripe.com/jobs/search?gh_jid=456'})
        third = first.model_copy(update={'source': 'source-c', 'external_id': '125', 'url': None})
        fourth = third.model_copy(update={'source': 'source-d'})
        for job in (first, second, third, fourth):
            db.save_job(self.conn, job)
        self.assertEqual(len(db.list_jobs(self.conn)), 4)
        self.assertEqual(self.conn.execute('SELECT count(*) AS n FROM source_jobs').fetchone()['n'], 4)

    def test_closure_retains_canonical_mapping_and_active_peer(self):
        first = self.job.model_copy()
        second = first.model_copy(update={'source': 'source-b', 'external_id': '875'})
        for job in (first, second):
            db.save_job(self.conn, job)
        initial = db.get_source_job(self.conn, first.source, first.external_id)
        closed = first.model_copy(update={'status': 'closed', 'url': None})
        db.save_job(self.conn, closed)
        after = db.get_source_job(self.conn, first.source, first.external_id)
        self.assertEqual(after.id, initial.id)
        self.assertEqual(after.canonical_job_id, initial.canonical_job_id)
        listing = db.get_job(self.conn, initial.canonical_job_id)
        self.assertEqual(listing.status, 'open')
        self.assertEqual(len(db.get_job_details(self.conn, listing.id).sources), 2)
        self.assertEqual(db.list_jobs(self.conn, source=first.source), [])
        db.save_job(self.conn, second.model_copy(update={'status': 'closed', 'url': None}))
        self.assertEqual(db.list_jobs(self.conn), [])
        self.assertIsNone(db.get_job(self.conn, initial.canonical_job_id))

    def test_url_correction_detaches_only_the_corrected_source(self):
        first = self.job.model_copy()
        second = first.model_copy(update={'source': 'source-b', 'external_id': '875'})
        db.save_job(self.conn, first)
        db.save_job(self.conn, second)
        before = db.get_source_job(self.conn, second.source, second.external_id)
        changed = second.model_copy(update={'url': 'https://example.com/jobs/different'})
        db.save_job(self.conn, changed)
        after = db.get_source_job(self.conn, second.source, second.external_id)
        self.assertEqual(after.id, before.id)
        self.assertEqual(after.first_seen_at, before.first_seen_at)
        self.assertNotEqual(after.canonical_job_id, before.canonical_job_id)
        self.assertEqual(db.get_source_job(self.conn, first.source, first.external_id).canonical_job_id, before.canonical_job_id)
        self.assertEqual(len(db.list_jobs(self.conn)), 2)

    def test_verified_redirect_bridges_existing_groups_and_old_ids_resolve(self):
        direct = self.job.model_copy(update={'url': 'https://employer.example/jobs/123'})
        wrapper = direct.model_copy(update={'source': 'source-b', 'external_id': '875', 'url': 'https://simplify.jobs/p/abc'})
        db.save_job(self.conn, direct)
        db.save_job(self.conn, wrapper)
        old_id = db.get_source_job(self.conn, wrapper.source, wrapper.external_id).canonical_job_id
        wrapper.enrichment_status = 'success'
        wrapper.description = 'Build software with Python.'
        wrapper.resolved_url = direct.url
        db.save_job(self.conn, wrapper)
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
                return db.save_job(conn, job)
        with ThreadPoolExecutor(max_workers=4) as pool:
            self.assertEqual(list(pool.map(save, range(4))), ['inserted'] * 4)
        self.assertEqual(len(db.list_source_jobs(self.conn)), 4)
        self.assertEqual(len(db.list_jobs(self.conn)), 1)
        self.assertEqual(self.conn.execute('SELECT count(*) AS n FROM canonical_jobs').fetchone()['n'], 1)


    def test_generic_career_url_does_not_merge_unrelated_source_records(self):
        first = self.job.model_copy(update={'url': 'https://example.com/careers'})
        second = first.model_copy(update={'source': 'source-b', 'external_id': '875'})
        db.save_job(self.conn, first)
        db.save_job(self.conn, second)
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
                rows = legacy.execute('SELECT id, first_seen_at, canonical_job_id FROM source_jobs ORDER BY id').fetchall()
                self.assertEqual([{key: row[key] for key in ('id', 'first_seen_at')} for row in rows], before)
                self.assertEqual(rows[0]['canonical_job_id'], rows[1]['canonical_job_id'])
                self.assertEqual(len(db.list_jobs(legacy)), 1)
                db.initialize(legacy)
                self.assertIsNone(legacy.execute("SELECT to_regclass('jobs') AS table_name").fetchone()['table_name'])
                self.assertEqual(legacy.execute('SELECT id, first_seen_at, canonical_job_id FROM source_jobs ORDER BY id').fetchall(), rows)
        finally:
            with psycopg.connect(os.environ['TEST_DATABASE_URL'], autocommit=True) as admin:
                admin.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(legacy_schema)))


    def test_failed_fetch_on_changed_url_does_not_reuse_old_redirect_or_description(self):
        old = self.job.model_copy(update={'enrichment_status': 'success', 'description': 'Python required.', 'resolved_url': self.job.url})
        peer = self.job.model_copy(update={'source': 'source-b', 'external_id': '875'})
        db.save_job(self.conn, old)
        db.save_job(self.conn, peer)
        previous = db.get_source_job(self.conn, old.source, old.external_id)
        changed = old.model_copy(update={'url': 'https://example.com/jobs/different', 'enrichment_status': 'failed'})
        db.save_job(self.conn, changed)
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
                self.assertIsNone(legacy.execute('SELECT canonical_job_id FROM source_jobs').fetchone()['canonical_job_id'])
                db.initialize(legacy)
                self.assertIsNotNone(legacy.execute('SELECT canonical_job_id FROM source_jobs').fetchone()['canonical_job_id'])
                self.assertIsNone(legacy.execute("SELECT to_regclass('jobs') AS name").fetchone()['name'])
                self.assertEqual(len(db.list_jobs(legacy)), 1)
        finally:
            with psycopg.connect(os.environ['TEST_DATABASE_URL'], autocommit=True) as admin:
                admin.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(legacy_schema)))


    def test_canonical_database_projection_has_exact_contract_columns(self):
        columns = self.conn.execute("SELECT column_name FROM information_schema.columns WHERE table_schema = current_schema() AND table_name = 'canonical_jobs' ORDER BY ordinal_position").fetchall()
        self.assertEqual([row['column_name'] for row in columns], ['id', 'company', 'title', 'location', 'application_url', 'posted_at', 'status'])
        db.save_job(self.conn, self.job)
        row = self.conn.execute('SELECT * FROM canonical_jobs').fetchone()
        self.assertEqual(row['application_url'], self.job.url)
        self.assertEqual(row['id'], db.get_source_job(self.conn, self.job.source, self.job.external_id).canonical_job_id)
