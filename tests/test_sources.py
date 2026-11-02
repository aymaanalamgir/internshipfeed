import unittest
from pathlib import Path

from internshipfeed.models import JobListing
from internshipfeed.normalize import normalize
from internshipfeed.sources import CANADIAN, SIMPLIFY

FIXTURES = Path(__file__).parent / 'fixtures'


class SourceTests(unittest.TestCase):
    def test_both_sources_share_one_record_and_job_contract(self):
        for source, file in ((CANADIAN, 'listings.md'), (SIMPLIFY, 'simplify.md')):
            records = source.parse((FIXTURES / file).read_text())
            self.assertEqual(len(records), 3)
            job = normalize(records[0], source.url, 'snapshot')
            self.assertIsInstance(job, JobListing)
            self.assertEqual(job.company, 'Acme')
            self.assertEqual(job.location, 'Toronto, ON / Remote, Canada')
            self.assertEqual(job.url, 'https://example.com/jobs/123?x=1&y=2')
            self.assertEqual(job.source, source.url)

    def test_simplify_dates_term_urls_and_identity(self):
        records = SIMPLIFY.parse((FIXTURES / 'simplify.md').read_text())
        first = normalize(records[0], SIMPLIFY.url, 'a')
        self.assertEqual(first.external_id, 'simplify:abc-123')
        self.assertEqual(first.term, 'Summer 2027')
        self.assertIsNone(first.posted_at)
        self.assertEqual(records[1].company, 'Acme')
        self.assertEqual(records[1].status, 'closed')
        self.assertEqual(normalize(records[1], SIMPLIFY.url, 'a').term, 'Winter 2027')
        self.assertIsNone(normalize(records[1], SIMPLIFY.url, 'a').posted_at)
        # Daily age changes do not change identity, even without a companion UUID.
        old = records[0].model_copy(update={'external_id': None})
        new = old.model_copy(update={'posted_at_raw': '3d'})
        self.assertEqual(normalize(old, SIMPLIFY.url, 'a').external_id, normalize(new, SIMPLIFY.url, 'b').external_id)
        self.assertEqual(records[2].url, 'https://simplify.jobs/p/only-link')

    def test_continuation_never_leaks_between_category_tables(self):
        markup = (FIXTURES / 'simplify.md').read_text().replace('<td>Other</td>', '<td>↳</td>')
        with self.assertRaisesRegex(ValueError, 'without company'):
            SIMPLIFY.parse(markup)

    def test_changed_table_shape_is_rejected(self):
        with self.assertRaises(ValueError):
            SIMPLIFY.parse('no job tables')
        with self.assertRaisesRegex(ValueError, 'Malformed'):
            SIMPLIFY.parse((FIXTURES / 'simplify.md').read_text().replace('<td>2d</td>', ''))

    def test_term_is_read_from_document_not_hardcoded(self):
        markup = (FIXTURES / 'simplify.md').read_text().replace('Summer 2027 Tech', 'Summer 2026 Tech')
        job = normalize(SIMPLIFY.parse(markup)[0], SIMPLIFY.url, 'a')
        self.assertEqual(job.term, 'Summer 2026')
