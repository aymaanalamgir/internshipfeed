"""Verify API input validation, access guards, and sanitized storage-failure responses."""
import os
import unittest
from unittest.mock import patch

import psycopg
from fastapi import HTTPException, Request
from fastapi.testclient import TestClient
from pydantic import ValidationError

from internshipfeed.api import app, connection, local_mutation
from internshipfeed.models import CandidateProfileUpdate
from pdf_fixtures import make_pdf


EMPTY_PROFILE = {
    "target_roles": [], "skills": [], "preferred_locations": [],
    "preferred_terms": [], "graduation_year": None,
}


class DeploymentMutationTests(unittest.TestCase):
    def request(self, origin='https://feed.example'):
        return Request({'type': 'http', 'method': 'POST', 'scheme': 'https', 'path': '/api/profile',
                        'root_path': '', 'query_string': b'', 'server': ('feed.example', 443),
                        'client': ('127.0.0.1', 9000),
                        'headers': [(b'host', b'feed.example'), (b'origin', origin.encode())]})

    def test_hosted_requests_cannot_bypass_local_guard_through_loopback_proxy(self):
        for environment in ('production', 'preview', ''):
            with patch.dict(os.environ, {'VERCEL': '1', 'VERCEL_ENV': environment}), self.assertRaises(HTTPException) as caught:
                local_mutation(self.request())
            self.assertEqual(caught.exception.status_code, 403)

    def test_vercel_development_retains_local_same_origin_actions(self):
        with patch.dict(os.environ, {'VERCEL': '1', 'VERCEL_ENV': 'development'}):
            local_mutation(self.request())
            with self.assertRaises(HTTPException) as caught:
                local_mutation(self.request('https://other.example'))
            self.assertEqual(caught.exception.status_code, 403)

    def test_only_vercel_development_uses_forwarded_public_origin(self):
        request = self.request()
        request.scope['headers'] = [(b'host', b'127.0.0.1:9000'), (b'origin', b'https://feed.example'),
                                    (b'x-forwarded-host', b'feed.example'), (b'x-forwarded-proto', b'https')]
        with patch.dict(os.environ, {'VERCEL': '1', 'VERCEL_ENV': 'development'}):
            local_mutation(request)
            bad = self.request('https://other.example')
            bad.scope['headers'].extend([(b'x-forwarded-host', b'feed.example'), (b'x-forwarded-proto', b'https')])
            with self.assertRaises(HTTPException) as caught:
                local_mutation(bad)
            self.assertEqual(caught.exception.status_code, 403)
        with patch.dict(os.environ, {'VERCEL': '0'}), self.assertRaises(HTTPException) as caught:
            local_mutation(request)
        self.assertEqual(caught.exception.status_code, 403)


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
    def test_interaction_storage_failures_return_clean_503(self):
        with patch('internshipfeed.api.db.connect', side_effect=psycopg.OperationalError('private database details')):
            with TestClient(app) as client:
                responses = [client.get('/api/interactions'), client.get('/api/saved'), client.get('/api/applied'),
                             client.post('/api/jobs/1/interactions', json={'type': 'save'})]
        for response in responses:
            self.assertEqual(response.status_code, 503)
            self.assertNotIn('private database details', response.text)

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
