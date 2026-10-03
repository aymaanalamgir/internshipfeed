"""Real PostgreSQL tests; run with scripts/test_with_postgres.py or TEST_DATABASE_URL."""
import json
import os
import tempfile
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
from internshipfeed.fetch import SOURCE_URL
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
        self.connect = lambda: db.connect(self.dsn)
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
        self.assertEqual(len(db.list_jobs(self.conn)), 1)
        with self.assertRaises(psycopg.errors.UniqueViolation):
            self.conn.execute("INSERT INTO jobs (source, external_id, company, title, location, status, snapshot_id, source_line) VALUES (%s, %s, 'x', 'y', 'z', 'open', 'a', 1)", (self.job.source, self.job.external_id))

    def test_closed_update_preserves_description_url_and_first_seen(self):
        self.job.description = "Experience with Python and React."
        self.job.keywords = JobKeywords(languages=["Python"], frameworks=["React"], requirements=["Experience with Python and React."])
        self.job.enrichment_status = "success"
        self.job.enriched_at = datetime.now(timezone.utc)
        self.assertEqual(db.save_job(self.conn, self.job), "inserted")
        before = db.list_jobs(self.conn)[0]
        closed = normalize(dict(self.records[0], status="closed", raw_url=None), SOURCE_URL, "b")
        self.assertEqual(db.save_job(self.conn, closed), "updated")
        after = db.list_jobs(self.conn)[0]
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
        after = db.list_jobs(self.conn)[0]
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
        with TestClient(app) as client:
            response = client.get("/api/jobs")
            self.assertEqual(response.status_code, 200)
            data = response.json()
            self.assertEqual(len(data), 1)
            detail = client.get("/api/jobs/%s" % data[0]["id"]).json()
            self.assertEqual(detail["description"], self.job.description)
            self.assertEqual(detail["keywords"]["frameworks"], ["React"])
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
                self.assertEqual(main(["--input", str(source), "--output", str(output)]), 0)
                first = json.loads((output / "run.json").read_text())
                self.assertEqual((first["scraped"], first["inserted"], first["skipped_duplicates"], first["failed"]), (4, 3, 1, 0))
                self.assertEqual(main(["--input", str(source), "--output", str(output)]), 0)
                second = json.loads((output / "run.json").read_text())
                self.assertEqual((second["inserted"], second["skipped_duplicates"], second["failed"]), (0, 4, 0))
            self.assertEqual(len(db.list_jobs(self.conn)), 3)
            self.assertEqual(db.latest_run(self.conn)["id"], second["id"])
            self.assertEqual(self.conn.execute("SELECT count(*) AS n FROM ingestion_runs").fetchone()["n"], 2)

    def test_one_bad_record_does_not_rollback_other_jobs(self):
        original_connect = db.connect
        bad = dict(self.records[0], company=" ")
        with tempfile.TemporaryDirectory() as directory:
            with patch("internshipfeed.cli.db.connect", side_effect=lambda: original_connect(self.dsn)), patch("internshipfeed.cli.parse_readme", return_value=[bad, self.records[1]]):
                result = main(["--input", str(FIXTURE), "--output", directory])
            report = json.loads((Path(directory) / "run.json").read_text())
            self.assertEqual(result, 1)
            self.assertEqual(report["status"], "partial_failure")
            self.assertEqual((report["scraped"], report["inserted"], report["failed"]), (2, 1, 1))
            self.assertEqual(len(db.list_jobs(self.conn)), 1)

    def test_enrichment_persisted_by_scraper(self):
        original_connect = db.connect
        enrichment = {"status": "success", "error": None, "description": "Experience with Python.", "keywords": {"languages": ["Python"]}, "extraction_method": "json_ld", "resolved_url": self.job.url}
        with tempfile.TemporaryDirectory() as directory:
            with patch("internshipfeed.cli.db.connect", side_effect=lambda: original_connect(self.dsn)), patch("internshipfeed.cli.enrich_job", return_value=enrichment):
                self.assertEqual(main(["--input", str(FIXTURE), "--output", directory, "--enrich"]), 0)
            job = next(j for j in db.list_jobs(self.conn) if j.status == "open")
            self.assertEqual(job.description, enrichment["description"])
            self.assertEqual(job.keywords.languages, ["Python"])
            self.assertIsNotNone(job.enriched_at)
