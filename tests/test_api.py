import unittest
from unittest.mock import patch

import psycopg
from fastapi.testclient import TestClient

from internshipfeed.api import app


class ApiFailureTests(unittest.TestCase):
    def test_unavailable_database_returns_503(self):
        with patch("internshipfeed.api.db.connect", side_effect=psycopg.OperationalError("connection unavailable")):
            with TestClient(app) as client:
                response = client.get("/api/jobs")
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("connection unavailable", response.text)
