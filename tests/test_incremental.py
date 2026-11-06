"""Exercise incremental source snapshots, safe closures, retries, and three-source lineage in PostgreSQL."""
import os
import tempfile
import time
import unittest
import uuid
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
from fastapi.testclient import TestClient

from internshipfeed import db
from internshipfeed.api import app
from internshipfeed.daily_refresh import DailySettings, run_daily_refresh
from internshipfeed.ingestion import SYNC_LOCK, run_ingestion
from internshipfeed.notifications import EmailConfig, EmailError
from internshipfeed.sources import CANADIAN, SIMPLIFY, SIMPLIFY_OFF_SEASON

FIXTURES=Path(__file__).parent/'fixtures'
CANADIAN_TEXT=(FIXTURES/'listings.md').read_text()
SIMPLIFY_TEXT=(FIXTURES/'simplify.md').read_text()
NEW_ROW='| New Company | Software Intern | Toronto | [Apply](https://example.com/jobs/new) | Nov 4, 2026 |\n'


@unittest.skipUnless(os.environ.get('TEST_DATABASE_URL'), 'Requires disposable PostgreSQL')
class IncrementalTests(unittest.TestCase):
    def setUp(self):
        self.schema='incremental_'+uuid.uuid4().hex
        with psycopg.connect(os.environ['TEST_DATABASE_URL'],autocommit=True) as conn:
            conn.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(self.schema)))
        dsn=make_conninfo(os.environ['TEST_DATABASE_URL'],options='-csearch_path='+self.schema)
        original=db.connect
        self.connect=lambda:original(dsn)
        self.conn=self.connect()
        db.initialize(self.conn)
        self.directory=tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.lock=-int(uuid.uuid4().hex[:14],16)
        for module in ('internshipfeed.ingestion','internshipfeed.api','internshipfeed.sync'):
            guard=patch(module+'.SYNC_LOCK',self.lock)
            guard.start(); self.addCleanup(guard.stop)

    def tearDown(self):
        self.conn.close()
        with psycopg.connect(os.environ['TEST_DATABASE_URL'],autocommit=True) as conn:
            conn.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(self.schema)))

    def ingest(self,text=CANADIAN_TEXT,source=CANADIAN,**options):
        path=Path(self.directory.name)/'input.md'
        path.write_text(text)
        return run_ingestion(conn=self.connect(),input_paths={source.key:path},source_keys=[source.key],
                             enrich=options.pop('enrich',False),write_files=False,**options)

    def test_parser_revision_repairs_unchanged_snapshot_once_without_duplicate_ids(self):
        text = (FIXTURES / 'simplify-off-season.md').read_text()
        configured = self.conn.execute("SELECT * FROM job_sources WHERE key='simplify-off-season'").fetchone()
        def legacy_parse(markdown):
            return SIMPLIFY_OFF_SEASON.parse(markdown.replace('<th>Terms</th>', '<th>Ignored</th>'))
        legacy_source = replace(SIMPLIFY_OFF_SEASON, parse=legacy_parse, parser_version=1)
        with patch('internshipfeed.ingestion.db.configured_sources', return_value=[(configured, legacy_source)]):
            first = self.ingest(text, SIMPLIFY_OFF_SEASON)
        # Historical successful reports have no parser_version key.
        self.conn.execute("UPDATE ingestion_runs SET report=report-'parser_version'")
        before = self.conn.execute('SELECT id,external_id FROM source_postings ORDER BY id').fetchall()
        canonical_ids = [row['id'] for row in self.conn.execute('SELECT id FROM canonical_jobs ORDER BY id')]
        self.assertTrue(all(row['term'] is None for row in self.conn.execute('SELECT term FROM source_postings')))
        second = self.ingest(text, SIMPLIFY_OFF_SEASON)
        self.assertEqual(second['status'], 'success')
        self.assertEqual(second['parsed'], 2)
        self.assertEqual(second['updated'], 2)
        self.assertEqual(second['inserted'], 0)
        self.assertEqual(second['new_canonical_job_ids'], [])
        self.assertEqual(second['sources'][0]['parser_version'], SIMPLIFY_OFF_SEASON.parser_version)
        self.assertEqual(first['sources'][0]['snapshot_id'], second['sources'][0]['snapshot_id'])
        self.assertEqual(before, self.conn.execute('SELECT id,external_id FROM source_postings ORDER BY id').fetchall())
        self.assertEqual(canonical_ids, [row['id'] for row in self.conn.execute('SELECT id FROM canonical_jobs ORDER BY id')])
        self.assertEqual([row['term'] for row in self.conn.execute('SELECT term FROM source_postings ORDER BY id')],
                         ['Winter 2027, Spring 2027', 'Fall 2026'])
        active = next(row for row in self.conn.execute("SELECT m.canonical_job_id FROM source_postings p JOIN canonical_job_sources m ON m.source_posting_id=p.id WHERE p.status='open'"))
        self.assertEqual(db.get_job_details(self.conn, active['canonical_job_id']).term, 'Winter 2027, Spring 2027')
        parser = Mock(side_effect=AssertionError('Already applied this parser revision'))
        source = replace(SIMPLIFY_OFF_SEASON, parse=parser)
        with patch('internshipfeed.ingestion.db.configured_sources', return_value=[(configured, source)]):
            third = self.ingest(text, SIMPLIFY_OFF_SEASON)
        self.assertEqual(third['unchanged_sources'], 1)
        self.assertEqual(third['parsed'], 0)
        parser.assert_not_called()

    def test_failed_parser_revision_retries_identical_content(self):
        self.ingest(SIMPLIFY_TEXT, SIMPLIFY)
        configured = self.conn.execute("SELECT * FROM job_sources WHERE key='simplify'").fetchone()
        version = SIMPLIFY.parser_version + 1
        broken = replace(SIMPLIFY, parser_version=version, parse=Mock(side_effect=ValueError('bad parser')))
        with patch('internshipfeed.ingestion.db.configured_sources', return_value=[(configured, broken)]):
            failed = self.ingest(SIMPLIFY_TEXT, SIMPLIFY)
        self.assertEqual(failed['status'], 'failed')
        parser = Mock(wraps=SIMPLIFY.parse)
        repaired = replace(SIMPLIFY, parser_version=version, parse=parser)
        with patch('internshipfeed.ingestion.db.configured_sources', return_value=[(configured, repaired)]):
            retried = self.ingest(SIMPLIFY_TEXT, SIMPLIFY)
        self.assertEqual(retried['status'], 'success')
        self.assertEqual(retried['parsed'], 3)
        self.assertEqual(retried['inserted'], 0)
        parser.assert_called_once()

    def test_unchanged_hash_skips_parser_enrichment_and_duplicates(self):
        first=self.ingest()
        ids=[r['id'] for r in self.conn.execute('SELECT id FROM canonical_jobs ORDER BY id')]
        parser=Mock(side_effect=AssertionError('Must not parse unchanged content'))
        configured=self.conn.execute("SELECT * FROM job_sources WHERE key='canadian-tech'").fetchone()
        with patch('internshipfeed.ingestion.db.configured_sources',return_value=[(configured,replace(CANADIAN,parse=parser))]), \
             patch('internshipfeed.ingestion.enrich_job') as enrich:
            second=self.ingest()
        self.assertEqual(second['status'],'success')
        self.assertEqual(second['parsed'],0)
        self.assertEqual(second['unchanged_sources'],1)
        self.assertEqual(second['inserted'],0)
        self.assertEqual(second['new_canonical_job_ids'],[])
        self.assertEqual(ids,[r['id'] for r in self.conn.execute('SELECT id FROM canonical_jobs ORDER BY id')])
        self.assertEqual(self.conn.execute('SELECT count(*) AS n FROM source_snapshots').fetchone()['n'],1)
        parser.assert_not_called();enrich.assert_not_called()

    def test_changed_source_adds_new_job_and_reuses_existing_description(self):
        self.ingest()
        self.conn.execute("UPDATE source_postings SET description='Stored Python description', enriched_at='2020-01-01', enrichment_status='success' WHERE company='Acme' AND status='open'")
        with patch('internshipfeed.ingestion.enrich_job',return_value={'status':'skipped'}) as enrich:
            result=self.ingest(CANADIAN_TEXT+NEW_ROW,enrich=True)
        self.assertEqual(result['status'],'success')
        self.assertEqual(result['inserted'],1)
        self.assertEqual(enrich.call_count,1)
        self.assertEqual(enrich.call_args.args[0].company,'New Company')
        self.assertEqual(self.conn.execute("SELECT description FROM source_postings WHERE company='Acme' AND status='open'").fetchone()['description'],'Stored Python description')

    def test_raw_age_only_changes_touch_lineage_without_application_fetch(self):
        self.ingest(SIMPLIFY_TEXT,SIMPLIFY)
        before=self.conn.execute("SELECT snapshot_id,last_seen_at FROM source_postings ORDER BY id LIMIT 1").fetchone()
        with patch('internshipfeed.ingestion.enrich_job') as enrich:
            result=self.ingest(SIMPLIFY_TEXT.replace('<td>2d</td>','<td>3d</td>'),SIMPLIFY,enrich=True)
        after=self.conn.execute('SELECT snapshot_id,last_seen_at,raw_posted_at FROM source_postings ORDER BY id LIMIT 1').fetchone()
        self.assertEqual(result['updated'],0)
        self.assertEqual(after['raw_posted_at'],'3d')
        self.assertNotEqual(before['snapshot_id'],after['snapshot_id'])
        self.assertGreater(after['last_seen_at'],before['last_seen_at'])
        enrich.assert_not_called()

    def test_removed_posting_closes_only_after_success_and_history_survives(self):
        self.ingest()
        last_seen=self.conn.execute("SELECT last_seen_at FROM source_postings WHERE company='Other'").fetchone()['last_seen_at']
        lines=CANADIAN_TEXT.splitlines(keepends=True)
        result=self.ingest(''.join(lines[:5]))
        self.assertEqual(result['removed'],1)
        self.assertEqual(self.conn.execute("SELECT status FROM source_postings WHERE company='Other'").fetchone()['status'],'closed')
        self.assertEqual(self.conn.execute('SELECT count(*) AS n FROM source_postings').fetchone()['n'],3)
        self.assertEqual(self.conn.execute("SELECT count(*) AS n FROM canonical_jobs WHERE status='closed'").fetchone()['n'],2)
        self.ingest(''.join(lines[:5]))
        self.assertEqual(self.conn.execute("SELECT last_seen_at FROM source_postings WHERE company='Other'").fetchone()['last_seen_at'],last_seen)

    def test_failed_parse_or_normalization_never_closes_missing_postings_and_retry_reparses(self):
        self.ingest()
        for broken in ['not a source', CANADIAN_TEXT.replace('Toronto, ON<br>Remote, Canada','')]:
            result=self.ingest(broken)
            self.assertEqual(result['status'],'failed')
            self.assertEqual(self.conn.execute("SELECT status FROM source_postings WHERE company='Other'").fetchone()['status'],'unknown')
        result=self.ingest(CANADIAN_TEXT+NEW_ROW)
        self.assertEqual(result['status'],'success')
        self.assertEqual(result['inserted'],1)

    def test_partial_storage_failure_does_not_close_missing_postings(self):
        self.ingest()
        text=''.join(CANADIAN_TEXT.splitlines(keepends=True)[:5])+NEW_ROW
        with patch('internshipfeed.ingestion.db.save_job',side_effect=RuntimeError('storage failure')):
            result=self.ingest(text)
        self.assertEqual(result['status'],'partial_failure')
        self.assertEqual(self.conn.execute("SELECT status FROM source_postings WHERE company='Other'").fetchone()['status'],'unknown')
        retry=self.ingest(text)
        self.assertEqual(retry['status'],'success')
        self.assertEqual(retry['removed'],1)

    def test_three_sources_keep_separate_identities_and_one_canonical_url(self):
        self.ingest()
        self.ingest(SIMPLIFY_TEXT,SIMPLIFY)
        self.ingest(SIMPLIFY_TEXT,SIMPLIFY_OFF_SEASON)
        grouped=self.conn.execute("SELECT count(*) AS n FROM canonical_job_sources m JOIN source_postings p ON p.id=m.source_posting_id WHERE p.application_url LIKE 'https://example.com/jobs/123%'").fetchone()['n']
        self.assertEqual(grouped,3)
        self.assertEqual(self.conn.execute("SELECT count(DISTINCT m.canonical_job_id) AS n FROM canonical_job_sources m JOIN source_postings p ON p.id=m.source_posting_id WHERE p.application_url LIKE 'https://example.com/jobs/123%'").fetchone()['n'],1)
        self.assertEqual(self.conn.execute("SELECT count(*) AS n FROM job_sources WHERE repository=%s",(SIMPLIFY.url,)).fetchone()['n'],2)
        self.assertIs(SIMPLIFY.parse,SIMPLIFY_OFF_SEASON.parse)
        db.initialize(self.conn)
        self.assertEqual(self.conn.execute("SELECT count(*) AS n FROM job_sources WHERE key='simplify-off-season' AND enabled").fetchone()['n'],1)

    def test_lock_contention_returns_busy_without_creating_batch_or_files(self):
        self.conn.execute('SELECT pg_advisory_lock(%s)',(self.lock,))
        result=self.ingest()
        self.assertEqual(result['status'],'busy')
        self.assertEqual(self.conn.execute('SELECT count(*) AS n FROM ingestion_batches').fetchone()['n'],0)
        self.conn.execute('SELECT pg_advisory_unlock(%s)',(self.lock,))
        self.assertEqual(self.ingest()['status'],'success')

    def test_url_change_reenriches_only_changed_job_and_metadata_commits_before_fetch(self):
        self.ingest(SIMPLIFY_TEXT,SIMPLIFY)
        changed=SIMPLIFY_TEXT.replace('https://example.com/jobs/123?x=1&amp;y=2', 'https://example.com/jobs/changed')
        # The fixture uses literal & in the application anchor.
        changed=changed.replace('https://example.com/jobs/123?x=1&y=2','https://example.com/jobs/changed')
        def enrich(job, cache):
            row=self.conn.execute('SELECT application_url FROM source_postings WHERE source_id=%s AND external_id=%s',
                                  (job.source_id,job.external_id)).fetchone()
            self.assertEqual(row['application_url'],job.url)
            return {'status':'failed','error':'temporary application page failure'}
        with patch('internshipfeed.ingestion.enrich_job',side_effect=enrich) as fetch:
            result=self.ingest(changed,SIMPLIFY,enrich=True)
        self.assertEqual(result['status'],'success')
        self.assertEqual(result['updated'],1)
        fetch.assert_called_once()

    def test_explicit_closure_skips_fetch_and_another_active_source_keeps_canonical_open(self):
        self.ingest(SIMPLIFY_TEXT,SIMPLIFY)
        self.ingest(SIMPLIFY_TEXT,SIMPLIFY_OFF_SEASON)
        configured=self.conn.execute("SELECT id FROM job_sources WHERE key='simplify-off-season'").fetchone()['id']
        job=db.get_source_job(self.conn,configured,'simplify:abc-123')
        closed=SIMPLIFY_TEXT.replace('<img alt="Simplify">', '<img alt="Simplify"> 🔒', 1)
        with patch('internshipfeed.ingestion.enrich_job') as fetch:
            result=self.ingest(closed,SIMPLIFY_OFF_SEASON,enrich=True)
        self.assertEqual(result['status'],'success')
        self.assertEqual(db.get_source_job(self.conn,configured,job.external_id).status,'closed')
        self.assertEqual(db.get_job(self.conn,job.canonical_job_id).status,'open')
        fetch.assert_not_called()

    def test_changed_title_refreshes_even_a_recent_description(self):
        self.ingest(SIMPLIFY_TEXT,SIMPLIFY)
        self.conn.execute("UPDATE source_postings SET description='Previous description', enriched_at=now(), enrichment_status='success' WHERE external_id='simplify:abc-123'")
        enrichment={'status':'success','description':'Updated requirements','keywords':{'languages':['Python']},
                    'extraction_method':'json_ld','resolved_url':'https://example.com/jobs/123?x=1&y=2'}
        with patch('internshipfeed.ingestion.enrich_job',return_value=enrichment) as fetch:
            result=self.ingest(SIMPLIFY_TEXT.replace('Software Engineer Intern','Software Platform Intern'),SIMPLIFY,enrich=True)
        self.assertEqual(result['status'],'success')
        fetch.assert_called_once()
        self.assertEqual(self.conn.execute("SELECT description FROM source_postings WHERE external_id='simplify:abc-123'").fetchone()['description'],'Updated requirements')

    def test_cron_mode_works_with_read_only_filesystem_and_database_url_only(self):
        with patch('internshipfeed.ingestion.Path.mkdir',side_effect=PermissionError), \
             patch.dict(os.environ,{'DATABASE_URL':'postgresql://test.invalid/unused'},clear=True):
            result=self.ingest()
        self.assertEqual(result['status'],'success')
        with patch('internshipfeed.api.db.connect',side_effect=self.connect),patch.dict(os.environ,{'VERCEL':'1'}):
            response=TestClient(app).get('/api/ingestion-status')
        self.assertFalse(response.json()['manual_sync_allowed'])
        self.assertIsNotNone(response.json()['last_successful_at'])
        self.ingest('broken')
        self.assertIsNotNone(db.sync_status(self.conn)['last_successful_at'])

    def test_deadline_keeps_committed_data_and_releases_lock(self):
        self.ingest()
        result=self.ingest(CANADIAN_TEXT+NEW_ROW,deadline=time.monotonic()-1)
        self.assertEqual(result['status'],'failed')
        self.assertEqual(self.conn.execute('SELECT count(*) AS n FROM source_postings').fetchone()['n'],3)
        self.assertEqual(self.ingest()['status'],'success')

    def test_email_configuration_and_failure_are_downstream_of_committed_ingestion(self):
        path=Path(self.directory.name)/'input.md';path.write_text(CANADIAN_TEXT)
        options={'source_keys':[CANADIAN.key],'input_paths':{CANADIAN.key:path},'enrich':False,'write_files':False}
        with patch('internshipfeed.daily_refresh.db.connect',side_effect=self.connect),patch.dict(os.environ,{},clear=True):
            result=run_daily_refresh(ingestion_options=options)
        self.assertEqual(result['ingestion_status'],'success')
        self.assertEqual(result['notification_status'],'skipped_unconfigured')
        path.write_text(CANADIAN_TEXT+NEW_ROW)
        with patch('internshipfeed.daily_refresh.db.connect',side_effect=self.connect), \
             patch('internshipfeed.daily_refresh.send_email',side_effect=EmailError('not accepted')):
            result=run_daily_refresh(DailySettings(0),ingestion_options=options,
                                     email_config=EmailConfig('test','from@example.com','to@example.com'))
        self.assertEqual(result['status'],'success')
        self.assertEqual(result['ingestion_status'],'success')
        self.assertEqual(result['notification_status'],'failed')
        self.assertEqual(db.get_batch(self.conn,result['batch_id'])['status'],'success')
        self.assertEqual(self.conn.execute("SELECT count(*) AS n FROM source_postings WHERE company='New Company'").fetchone()['n'],1)
