import unittest
from datetime import datetime, timezone
from unittest.mock import Mock, patch
from urllib.error import HTTPError

from internshipfeed.ai import DEFAULT_MODEL, PROMPT_VERSION, DescriptionAnalysis, description_hash
from internshipfeed.enrich import enrich_job
from internshipfeed.ingestion import new_run, process_description
from internshipfeed.models import JobListing


def listing(status='open'):
    return JobListing(company='Acme', title='Intern', location='Toronto', url='https://example.com/jobs/1', source='test', external_id='abc', status=status, snapshot_id='snapshot', source_line=1)


class WorkflowTests(unittest.TestCase):
    def test_closed_jobs_do_not_fetch_or_use_ai(self):
        transformer = Mock()
        with patch('internshipfeed.ingestion.enrich_job') as fetch:
            run = new_run()
            process_description(listing('closed'), None, run, {}, transformer, True, None, False, 0, True)
        fetch.assert_not_called()
        transformer.transform.assert_not_called()
        self.assertEqual(run['closed'], 1)

    def test_dead_application_not_robots_is_hidden(self):
        error = HTTPError('https://example.com/jobs/1', 410, 'Gone', {}, None)
        with patch('internshipfeed.enrich.fetch_text', side_effect=[('User-agent: *\nAllow: /', ''), error]):
            result = enrich_job(listing())
        self.assertTrue(result['dead'])
        transformer = Mock()
        job, run = listing(), new_run()
        with patch('internshipfeed.ingestion.enrich_job', return_value=result):
            process_description(job, None, run, {}, transformer, True, None, False, 0, True)
        self.assertEqual(job.status, 'dead')
        self.assertEqual(run['dead_links'], 1)
        transformer.transform.assert_not_called()

    def test_timeout_does_not_mark_link_dead(self):
        with patch('internshipfeed.enrich.fetch_text', side_effect=TimeoutError()):
            result = enrich_job(listing())
        self.assertEqual(result['status'], 'failed')
        self.assertNotIn('dead', result)

    def test_cached_description_and_ai_reused(self):
        old = listing()
        old.description = 'Build tools with Python.'
        old.enriched_at = datetime.now(timezone.utc)
        old.enrichment_status = 'success'
        old.ai_analysis = DescriptionAnalysis(overview='Build tools.', key_requirements=[], preferred_qualifications=[], tech_stack=[], frameworks=[], responsibilities=[])
        old.ai_description_hash = description_hash(old.description)
        old.ai_model = DEFAULT_MODEL
        old.ai_prompt_version = PROMPT_VERSION
        old.ai_status = 'success'
        transformer = Mock(model=DEFAULT_MODEL)
        job, run = listing(), new_run()
        with patch('internshipfeed.ingestion.enrich_job') as fetch:
            process_description(job, old, run, {}, transformer, True, None, False, 0, True)
        fetch.assert_not_called()
        transformer.transform.assert_not_called()
        self.assertEqual(job.ai_analysis, old.ai_analysis)
        self.assertEqual(run['ai_cached'], 1)

    def test_recent_dead_link_not_refetched(self):
        old = listing('dead')
        old.link_checked_at = datetime.now(timezone.utc)
        job, run = listing(), new_run()
        transformer = Mock()
        with patch('internshipfeed.ingestion.enrich_job') as fetch:
            process_description(job, old, run, {}, transformer, True, None, False, 0, True)
        fetch.assert_not_called()
        self.assertEqual(job.status, 'dead')
