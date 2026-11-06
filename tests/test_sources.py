"""Verify registered source parsers produce consistent records, identities, and terms."""
import unittest
from datetime import date
from pathlib import Path

from internshipfeed.models import JobListing
from internshipfeed.normalize import normalize
from internshipfeed.sources import CANADIAN, SIMPLIFY, SIMPLIFY_OFF_SEASON

FIXTURES = Path(__file__).parent / 'fixtures'


class SourceTests(unittest.TestCase):
    def test_off_season_plural_and_singular_terms_survive_normalization(self):
        markup = (FIXTURES / 'simplify-off-season.md').read_text()
        for header in ('Terms', 'Term'):
            with self.subTest(header=header):
                records = SIMPLIFY_OFF_SEASON.parse(markup.replace('<th>Terms</th>', '<th>' + header + '</th>'))
                job = normalize(records[0], SIMPLIFY_OFF_SEASON.url, 'a')
                self.assertEqual(job.term, 'Winter 2027, Spring 2027')
                self.assertEqual(job.external_id, 'simplify:off-season-123')
                self.assertEqual(job.url, 'https://example.com/jobs/123?x=1&y=2')
                self.assertIsNone(job.posted_at)
                self.assertEqual(records[1].term, 'Fall 2026')
                self.assertEqual(records[1].status, 'closed')

    def test_plural_terms_fix_preserves_linkless_legacy_source_identity(self):
        markup = (FIXTURES / 'simplify-off-season.md').read_text()
        old = SIMPLIFY_OFF_SEASON.parse(markup.replace('<th>Terms</th>', '<th>Ignored</th>'))
        current = SIMPLIFY_OFF_SEASON.parse(markup)
        for before, after in zip(old, current):
            with self.subTest(company=after.company):
                self.assertIsNone(before.term)
                self.assertIsNotNone(after.term)
                self.assertEqual(normalize(before, SIMPLIFY_OFF_SEASON.url, 'a').external_id,
                                 normalize(after, SIMPLIFY_OFF_SEASON.url, 'b').external_id)

    def test_simplify_month_ages_are_relative_and_stay_undated(self):
        markup = (FIXTURES / 'simplify.md').read_text()
        for age in ('0d', '2h', '3w', '1m', '1mo', '12mo'):
            with self.subTest(age=age):
                record = SIMPLIFY.parse(markup.replace('<td>2d</td>', '<td>' + age + '</td>'))[0]
                self.assertEqual(record.date_kind, 'relative')
                job = normalize(record, SIMPLIFY.url, 'a')
                self.assertIsNone(job.posted_at)
                self.assertEqual(job.raw_posted_at, age)

    def test_off_season_uses_shared_parser_with_explicit_terms_and_archived_missing_heading(self):
        self.assertIs(SIMPLIFY_OFF_SEASON.parse, SIMPLIFY.parse)
        markup = (FIXTURES / 'simplify.md').read_text()
        markup = markup.replace('<td><a href="https://example.com/jobs/123', '<td>Winter 2027, Spring 2027</td><td><a href="https://example.com/jobs/123')
        record = SIMPLIFY_OFF_SEASON.parse(markup)[0]
        self.assertEqual(record.term, 'Winter 2027, Spring 2027')
        self.assertEqual(record.posted_at_raw, '2d')
        self.assertEqual(record.status, 'open')
        self.assertEqual(record.url, 'https://example.com/jobs/123?x=1&y=2')

    def test_archived_off_season_rows_without_urls_keep_distinct_term_identities(self):
        header='<table><tr><th>Company</th><th>Role</th><th>Location</th><th>Application</th><th>Age</th></tr>'
        row='<tr><td>Acme</td><td>Intern</td><td>Toronto</td><td>{}</td><td>🔒</td><td>2mo</td></tr>'
        records=SIMPLIFY_OFF_SEASON.parse(header+row.format('Winter 2026')+row.format('Winter 2027')+'</table>')
        self.assertNotEqual(records[0].external_id,records[1].external_id)
        self.assertTrue(all(record.status=='closed' for record in records))

    def test_exact_dates_from_both_parsers_and_raw_metadata(self):
        canadian = CANADIAN.parse((FIXTURES / 'listings.md').read_text())[0]
        self.assertEqual(normalize(canadian, CANADIAN.url, 'a').posted_at, date(2026, 10, 2))
        for raw in ('Nov 03, 2026', 'November 03, 2026', '2026-11-03'):
            markup = (FIXTURES / 'simplify.md').read_text().replace('<td>2d</td>', '<td>' + raw + '</td>')
            record = SIMPLIFY.parse(markup)[0]
            job = normalize(record, SIMPLIFY.url, 'a')
            self.assertEqual(record.date_kind, 'exact')
            self.assertEqual(record.posted_at_raw, raw)
            self.assertEqual(job.raw_posted_at, raw)
            self.assertEqual(job.posted_at, date(2026, 11, 3))

    def test_missing_yearless_relative_and_malformed_dates_remain_unknown(self):
        for source, filename, old, new in (
            (CANADIAN, 'listings.md', 'Oct 2, 2026', ''),
            (CANADIAN, 'listings.md', 'Oct 2, 2026', 'Feb 30, 2026'),
            (CANADIAN, 'listings.md', 'Oct 2, 2026', 'Nov 03'),
            (SIMPLIFY, 'simplify.md', '<td>2d</td>', '<td>Feb 30, 2026</td>'),
            (SIMPLIFY, 'simplify.md', '<td>2d</td>', '<td></td>'),
        ):
            markup = (FIXTURES / filename).read_text().replace(old, new)
            job = normalize(source.parse(markup)[0], source.url, 'a')
            self.assertIsNone(job.posted_at)
        record = SIMPLIFY.parse((FIXTURES / 'simplify.md').read_text())[0]
        self.assertEqual(record.date_kind, 'relative')
        self.assertEqual(normalize(record, SIMPLIFY.url, 'a').raw_posted_at, '2d')

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
