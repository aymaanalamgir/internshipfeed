import json
import unittest
from datetime import date
from pydantic import ValidationError
from pathlib import Path
from unittest.mock import patch

from internshipfeed.enrich import enrich_job, extract_description, extract_keywords
from internshipfeed.fetch import SOURCE_URL
from internshipfeed.normalize import normalize
from internshipfeed.source import parse_readme

FIXTURE = Path(__file__).parent / "fixtures" / "listings.md"


class IngestionTests(unittest.TestCase):
    def setUp(self):
        self.records = parse_readme(FIXTURE.read_text())

    def test_nested_badge_and_continuation(self):
        first, second, third = self.records
        self.assertEqual(first["raw_url"], "https://example.com/jobs/123?x=1&y=2")
        self.assertEqual(first["company"], "Acme")
        self.assertEqual(first["location"], "Toronto, ON / Remote, Canada")
        self.assertEqual(second["company"], "Acme")
        self.assertEqual(second["status"], "closed")
        self.assertIsNone(second["raw_url"])
        self.assertEqual(third["status"], "unknown")

    def test_normalization_missing_values_and_stable_identity(self):
        first = normalize(self.records[0], SOURCE_URL, "a")
        self.assertEqual(first.term, "Winter 2027")
        self.assertEqual(first.posted_at, date(2026, 10, 2))
        closed = dict(self.records[0], raw_url=None, status="closed")
        self.assertEqual(first.external_id, normalize(closed, SOURCE_URL, "b").external_id)
        third = normalize(self.records[2], SOURCE_URL, "a")
        self.assertIsNone(third.term)
        self.assertIsNone(third.posted_at)

    def test_source_format_failures(self):
        with self.assertRaises(ValueError):
            parse_readme("no table")
        with self.assertRaises(ValueError):
            parse_readme(FIXTURE.read_text().replace("**Acme**", "↳"))
        with self.assertRaises(ValueError):
            parse_readme(FIXTURE.read_text().replace("| Other | Developer Intern", "| Other | unexpected | Developer Intern"))

    def test_description_and_keywords(self):
        description = '<h2>Requirements</h2><ul><li>Experience with Python, React, PostgreSQL and C++.</li><li>Currently enrolled in a computer science degree.</li></ul><h2>Responsibilities</h2><p>Build software using Docker.</p>'
        markup = '<script type="application/ld+json">' + json.dumps({"@graph": [{"@type": "JobPosting", "description": description}]}) + '</script>'
        text, method = extract_description(markup)
        self.assertEqual(method, "json_ld")
        keywords = extract_keywords(text)
        self.assertEqual(keywords["languages"], ["Python", "C++"])
        self.assertEqual(keywords["frameworks"], ["React"])
        self.assertEqual(len(keywords["requirements"]), 2)
        self.assertNotIn("R", extract_keywords("R&D and R/S")["languages"])
        self.assertEqual(extract_keywords("## Requirements\nExperience building APIs with Python.\n## Our culture\nWe value collaboration and openness.")["requirements"], ["Experience building APIs with Python."])
        self.assertNotIn("Java", extract_keywords("JavaScript")['languages'])
        self.assertEqual(extract_description('<main>Please enable JavaScript</main>'), (None, None))

    def test_html_fallback(self):
        markup = '<nav>Python</nav><main><h2>Requirements</h2><p>' + ('Experience with Java and React. ' * 10) + '</p><h2>Benefits</h2><p>We offer comprehensive health insurance.</p></main>'
        text, method = extract_description(markup)
        self.assertEqual(method, "html_main")
        self.assertNotIn("Python", text)
        self.assertEqual(len(extract_keywords(text)["requirements"]), 1)

    def test_enrichment_success(self):
        job = normalize(self.records[0], SOURCE_URL, "a")
        markup = '<script type="application/ld+json">' + json.dumps({"@type": "JobPosting", "description": "<h2>Requirements</h2><p>Experience with Python and Docker.</p>"}) + '</script>'
        with patch("internshipfeed.enrich.fetch_text", side_effect=[("User-agent: *\nAllow: /", ""), (markup, job.url)]):
            result = enrich_job(job)
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["keywords"]["languages"], ["Python"])

    def test_enrichment_failure_does_not_change_listing_status(self):
        job = normalize(self.records[0], SOURCE_URL, "a")
        with patch("internshipfeed.enrich.fetch_text", side_effect=TimeoutError("timeout")):
            result = enrich_job(job)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(job.status, "open")

    def test_robots_disallow(self):
        job = normalize(self.records[0], SOURCE_URL, "a")
        with patch("internshipfeed.enrich.fetch_text", return_value=("User-agent: *\nDisallow: /jobs", "")) as fetch:
            result = enrich_job(job)
            self.assertEqual(fetch.call_count, 1)
        self.assertEqual(result["status"], "skipped")

    def test_model_rejects_bad_fields(self):
        job = normalize(self.records[0], SOURCE_URL, "a")
        with self.assertRaises(ValidationError):
            job.url = "javascript:alert(1)"
        with self.assertRaises(ValidationError):
            job.company = " "
        with self.assertRaises(ValidationError):
            job.status = "maybe"


if __name__ == "__main__":
    unittest.main()
