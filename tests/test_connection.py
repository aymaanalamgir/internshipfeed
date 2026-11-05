"""Verify pasted database settings are normalized without altering credentials or connection options."""
import os
import unittest
from unittest.mock import patch

from internshipfeed import db


URL = 'postgresql://user:example%20password@example.test/internshipfeed?sslmode=require'


class ConnectionConfigurationTests(unittest.TestCase):
    def setUp(self):
        for target in (patch.dict(os.environ, {}, clear=True), patch('internshipfeed.db.load_dotenv')):
            target.start()
            self.addCleanup(target.stop)
        driver = patch('internshipfeed.db.psycopg.connect')
        self.driver = driver.start()
        self.addCleanup(driver.stop)

    def test_environment_url_trims_accidental_line_endings_and_outer_spaces(self):
        for value in (URL + '\n', '\r\n' + URL + '\r\n', ' \t' + URL + '\t '):
            with self.subTest(value=repr(value)), patch.dict(os.environ, {'DATABASE_URL': value}):
                self.assertIs(db.connect(), self.driver.return_value)
                self.driver.assert_called_with(URL, row_factory=db.dict_row, autocommit=True, connect_timeout=5)

    def test_explicit_url_is_trimmed_and_takes_precedence_over_environment(self):
        with patch.dict(os.environ, {'DATABASE_URL': 'postgresql://unused.example.test/other'}):
            db.connect('  ' + URL + '\n')
        self.assertEqual(self.driver.call_args.args[0], URL)

    def test_missing_or_whitespace_only_settings_raise_a_clear_configuration_error(self):
        for value in ('', ' \t\r\n'):
            with patch.dict(os.environ, {'DATABASE_URL': value}), self.assertRaisesRegex(RuntimeError, 'DATABASE_URL is required'):
                db.connect()
        with self.assertRaisesRegex(RuntimeError, 'DATABASE_URL is required'):
            db.connect()
        self.driver.assert_not_called()

    def test_encoded_url_content_and_quoted_keyword_values_are_preserved(self):
        for value in (URL + '&application_name=Internship%20Feed',
                      "host=example.test dbname=internshipfeed password=' keep spaces '"):
            with self.subTest(value=value):
                db.connect('\n' + value + '\n')
                self.assertEqual(self.driver.call_args.args[0], value)
