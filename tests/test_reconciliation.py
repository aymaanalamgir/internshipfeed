"""Verify the canonical job contract and conservative application-URL normalization."""
import unittest
from pydantic import ValidationError
from internshipfeed.models import CanonicalJob
from internshipfeed.reconciliation import canonicalize_url, matching_url


class CanonicalUrlTests(unittest.TestCase):
    def test_tracking_transport_and_query_order(self):
        first = 'HTTP://Jobs.Example.com:80/jobs/123/?utm_source=Simplify&ref=Simplify&b=2&a=1#apply'
        second = 'https://jobs.example.com/jobs/123?a=1&b=2'
        self.assertEqual(canonicalize_url(first), canonicalize_url(second))

    def test_job_query_ids_preserved(self):
        self.assertNotEqual(canonicalize_url('https://stripe.com/jobs/search?gh_jid=123'),
                            canonicalize_url('https://stripe.com/jobs/search?gh_jid=456'))
        self.assertIn('jobId=ABC', canonicalize_url('https://example.com/apply?jobId=ABC&utm_medium=github'))

    def test_path_case_and_encoded_slashes_preserved(self):
        self.assertNotEqual(canonicalize_url('https://jobs.example.com/ABC'), canonicalize_url('https://jobs.example.com/abc'))
        self.assertNotEqual(canonicalize_url('https://example.com/a%2Fb'), canonicalize_url('https://example.com/a/b'))
        self.assertEqual(canonicalize_url('https://example.com/%7Euser'), canonicalize_url('https://example.com/~user/'))

    def test_hash_routes_not_discarded(self):
        self.assertNotEqual(canonicalize_url('https://example.com/#/jobs/123'), canonicalize_url('https://example.com/#/jobs/456'))

    def test_duplicate_query_parameter_order_preserved(self):
        self.assertNotEqual(canonicalize_url('https://example.com/?id=1&id=2'), canonicalize_url('https://example.com/?id=2&id=1'))

    def test_invalid_or_missing_urls_never_form_a_shared_key(self):
        for url in (None, '', 'javascript:alert(1)', 'not-a-url', 'https://user:pass@example.com/job'):
            self.assertIsNone(canonicalize_url(url))


    def test_generic_career_pages_are_not_matching_evidence(self):
        for value in ('https://example.com/careers?utm_source=github', 'https://example.com/jobs/search?q=intern', 'https://jobs.example.com/'):
            self.assertIsNone(matching_url(value))
        self.assertIsNotNone(matching_url('https://example.com/jobs/search?gh_jid=123'))
        self.assertIsNotNone(matching_url('https://example.com/#/jobs/123'))


class CanonicalContractTests(unittest.TestCase):
    def test_exact_seven_field_model(self):
        expected = ['id', 'company', 'title', 'location', 'application_url', 'posted_at', 'status']
        self.assertEqual(list(CanonicalJob.model_fields), expected)
        job = CanonicalJob(id=420, company='Acme', title='Intern', location='Toronto', application_url=None, posted_at=None, status='unknown')
        self.assertEqual(list(job.model_dump()), expected)
        with self.assertRaises(ValidationError):
            CanonicalJob.model_validate(dict(job.model_dump(), description='Unexpected canonical field'))
        with self.assertRaises(ValidationError):
            CanonicalJob.model_validate(dict(job.model_dump(), application_url='javascript:alert(1)'))
