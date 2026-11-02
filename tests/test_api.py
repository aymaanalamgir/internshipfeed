import unittest
from unittest.mock import patch

import psycopg
from fastapi.testclient import TestClient
from pydantic import ValidationError

from internshipfeed.api import app, connection
from internshipfeed.models import CandidateProfileUpdate
from pdf_fixtures import make_pdf


EMPTY_PROFILE = {
    "target_roles": [], "skills": [], "preferred_locations": [],
    "preferred_terms": [], "graduation_year": None,
}


class ProfileValidationTests(unittest.TestCase):
    def test_lists_trim_blanks_and_exact_duplicates_without_changing_case_or_order(self):
        values = [" SQL ", "Python", "Python ", "", "  ", "python", "SQL"]
        fields = {key: values for key in EMPTY_PROFILE if key != "graduation_year"}
        result = CandidateProfileUpdate(**dict(fields, graduation_year=None))
        for key in fields:
            self.assertEqual(getattr(result, key), ["SQL", "Python", "python"])

    def test_lists_reject_non_lists_and_non_string_items(self):
        for key in EMPTY_PROFILE.keys() - {"graduation_year"}:
            for value in ("Python", None, {}, 7, ("Python",), [7], [True], [None], [[]]):
                with self.subTest(field=key, value=value), self.assertRaises(ValidationError):
                    CandidateProfileUpdate(**dict(EMPTY_PROFILE, **{key: value}))

    def test_graduation_year_is_nullable_strict_and_within_database_range(self):
        for year in (None, 1900, 2027, 2200):
            with self.subTest(year=year):
                self.assertEqual(CandidateProfileUpdate(**dict(EMPTY_PROFILE, graduation_year=year)).graduation_year, year)
        for year in (1899, 2201, 3000, "2027", 2027.0, True):
            with self.subTest(year=year), self.assertRaises(ValidationError):
                CandidateProfileUpdate(**dict(EMPTY_PROFILE, graduation_year=year))

    def test_all_editable_fields_required_and_other_fields_forbidden(self):
        for key in EMPTY_PROFILE:
            payload = {field: value for field, value in EMPTY_PROFILE.items() if field != key}
            with self.subTest(missing=key), self.assertRaises(ValidationError):
                CandidateProfileUpdate(**payload)
        for key in ("id", "created_at", "updated_at", "admin", "favorite_color"):
            with self.subTest(extra=key), self.assertRaises(ValidationError):
                CandidateProfileUpdate(**dict(EMPTY_PROFILE, **{key: "unexpected"}))


class ApiFailureTests(unittest.TestCase):
    def test_unavailable_database_returns_503(self):
        with patch("internshipfeed.api.db.connect", side_effect=psycopg.OperationalError("connection unavailable")):
            with TestClient(app) as client:
                response = client.get("/api/jobs")
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("connection unavailable", response.text)

    def test_profile_storage_failures_return_503_without_database_details(self):
        with patch("internshipfeed.api.db.connect", side_effect=psycopg.OperationalError("private PostgreSQL details")):
            with TestClient(app) as client:
                responses = [client.get("/api/profile"), client.put("/api/profile", json=EMPTY_PROFILE)]
        for response in responses:
            self.assertEqual(response.status_code, 503)
            self.assertNotIn("private PostgreSQL details", response.text)

    def test_resume_unavailable_database_returns_503(self):
        with patch('internshipfeed.api.db.connect', side_effect=psycopg.OperationalError('private database details')):
            with TestClient(app) as client:
                response = client.post('/api/resumes', files={'file': ('resume.pdf', make_pdf(['Sample Candidate']), 'application/pdf')})
        self.assertEqual(response.status_code, 503)
        self.assertNotIn('private database details', response.text)

    def test_recommendations_unavailable_database_returns_503(self):
        with patch('internshipfeed.api.db.connect', side_effect=psycopg.OperationalError('private database details')):
            with TestClient(app) as client:
                response = client.get('/api/recommendations')
        self.assertEqual(response.status_code, 503)
        self.assertNotIn('private database details', response.text)

    def test_resume_insert_failure_does_not_log_document_or_database_details(self):
        private_text = 'Private Candidate Resume Content'
        with patch.dict(app.dependency_overrides, {connection: lambda: object()}), \
             patch('internshipfeed.api.db.connect', side_effect=psycopg.OperationalError('offline')), \
             patch('internshipfeed.api.db.development_profile', return_value={'id': 1}), \
             patch('internshipfeed.api.db.save_resume_text', side_effect=psycopg.DataError(private_text)), TestClient(app) as client:
            with self.assertLogs('internshipfeed.api', level='WARNING') as logs:
                response = client.post('/api/resumes', files={'file': ('resume.pdf', make_pdf([private_text]), 'application/pdf')})
        self.assertEqual(response.status_code, 503)
        self.assertNotIn(private_text, response.text)
        self.assertNotIn(private_text, '\n'.join(logs.output))
