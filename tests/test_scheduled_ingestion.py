"""Verify cron authentication, inline completion, configuration isolation, and safe responses."""
import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from internshipfeed.api import app
from internshipfeed.scheduled_ingestion import run_scheduled_ingestion


class ScheduledIngestionTests(unittest.TestCase):
    def test_cron_rejects_missing_wrong_and_unconfigured_secrets_before_db_access(self):
        with patch.dict(os.environ, {'CRON_SECRET': 'test-only-secret'}), \
             patch('internshipfeed.api.run_scheduled_ingestion') as ingest, \
             patch('internshipfeed.api.db.connect') as connect:
            client = TestClient(app)
            for header in ({}, {'Authorization': 'Bearer wrong'}, {'Authorization': 'test-only-secret'}):
                self.assertEqual(client.get('/api/cron/ingest', headers=header).status_code, 401)
            with patch.dict(os.environ, {'CRON_SECRET': ''}):
                self.assertEqual(client.get('/api/cron/ingest', headers={'Authorization': 'Bearer test-only-secret'}).status_code, 401)
            ingest.assert_not_called()
            connect.assert_not_called()

    def test_cron_finishes_inline_without_resend_and_does_not_use_local_mutation(self):
        completed = []
        def ingest():
            completed.append(True)
            return {'status': 'success', 'inserted': 2}
        with patch.dict(os.environ, {'CRON_SECRET': 'test-only-secret', 'VERCEL': '1'}, clear=True), \
             patch('internshipfeed.api.run_scheduled_ingestion', side_effect=ingest), \
             patch('internshipfeed.api.start_sync') as background, \
             patch('internshipfeed.daily_refresh.EmailConfig.from_env') as email:
            response = TestClient(app).get('/api/cron/ingest', headers={'Authorization': 'Bearer test-only-secret'})
        self.assertEqual(completed, [True])
        self.assertEqual(response.status_code, 200)
        self.assertIn('no-store', response.headers['cache-control'])
        background.assert_not_called()
        email.assert_not_called()

    def test_cron_reports_busy_and_failures_without_raw_data(self):
        with patch.dict(os.environ, {'CRON_SECRET': 'test-only-secret'}):
            for state, code in [('busy', 200), ('partial_failure', 503), ('failed', 503)]:
                with patch('internshipfeed.api.run_scheduled_ingestion', return_value={'status': state}):
                    response=TestClient(app).get('/api/cron/ingest', headers={'Authorization': 'Bearer test-only-secret'})
                self.assertEqual(response.status_code, code)
                self.assertEqual(response.json(), {'status': state})

    def test_scheduled_orchestration_is_bounded_has_no_files_and_returns_only_counts(self):
        report={'id': 'test', 'status': 'success', 'sources': [{}, {}, {}],
                'scraped': 5, 'inserted': 2, 'new_canonical_job_ids': [7], 'errors': ['private diagnostics']}
        with patch('internshipfeed.scheduled_ingestion.run_ingestion', return_value=report) as ingest:
            result=run_scheduled_ingestion()
        self.assertFalse(ingest.call_args.kwargs['write_files'])
        self.assertIsNotNone(ingest.call_args.kwargs['deadline'])
        self.assertEqual(result['sources'], 3)
        self.assertEqual(result['new_canonical_jobs'], 1)
        self.assertNotIn('errors', result)
        self.assertNotIn('new_canonical_job_ids', result)
