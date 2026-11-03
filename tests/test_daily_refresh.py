"""Verify email rendering, the mocked Resend boundary, and daily-command configuration."""
import io
import os
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from internshipfeed.daily_refresh import DailySettings, main
from internshipfeed.models import CanonicalJob, RankedMatchResult
from internshipfeed.notifications import EmailConfig, EmailError, format_email, send_email

MESSAGE_ID = '145ea166-6aca-43d3-b749-c203049cc9d1'
CONFIG = EmailConfig('test-secret', 'InternshipFeed <sender@example.com>', 'recipient@example.com')


def match(job_id=1, score=90, **changes):
    data = dict(id=job_id, company='Acme', title='SWE Intern', location='Toronto',
                application_url='https://example.com/' + str(job_id), posted_at=None, status='open')
    data.update(changes)
    return RankedMatchResult(job=CanonicalJob(**data), score=80, profile_score=80, behavior_score=90,
                             final_score=score, role_score=40, skills_score=30, location_score=0, term_score=10,
                             matched_skills=['Python', 'PostgreSQL'], reasons=[], behavior_reasons=[])


class NotificationFormatTests(unittest.TestCase):
    def test_job_fields_rank_order_and_top_n(self):
        digest = format_email([match(3, 91), match(2, 75), match(1, 52)], 2, {3: 'Summer 2027'})
        self.assertEqual(digest.job_ids, (3, 2))
        self.assertEqual(digest.subject, 'InternshipFeed — 2 new matches')
        for value in ('91% match', 'Acme', 'SWE Intern', 'Toronto', 'Summer 2027', 'Python', 'PostgreSQL', 'https://example.com/3'):
            self.assertIn(value, digest.text)
            self.assertIn(value, digest.html)
        self.assertNotIn('52%', digest.text)
        self.assertLess(digest.html.index('91% match'), digest.html.index('75% match'))
        for private in ('canonical_job_id', 'behavior_score', 'profile_score', 'resume'):
            self.assertNotIn(private, digest.html)
        self.assertEqual(format_email([match()] * 3, 5).job_ids, (1,))

    def test_empty_and_unsafe_links(self):
        self.assertEqual(format_email([], 5).text, '')
        for url in (None, 'javascript:alert(1)', 'https://user:password@example.com/job', 'https://example.com/ bad', 'https://[invalid'):
            self.assertEqual(format_email([match().model_copy(update={'job': match().job.model_copy(update={'application_url': url})})], 5).job_ids, ())

    def test_escape_html_and_keep_complete_urls(self):
        url = 'https://example.com/?a="quoted"&b=2'
        digest = format_email([match(company='<script>x</script>', title='SWE & Data', application_url=url)], 5,
                              {1: '<Summer>'}, 'https://app.example.com/')
        self.assertIn('&lt;script&gt;', digest.html)
        self.assertIn('SWE &amp; Data', digest.html)
        self.assertIn('a=&quot;quoted&quot;&amp;b=2', digest.html)
        self.assertNotIn('<script>', digest.html)
        self.assertIn(url, digest.text)
        self.assertIn('Open InternshipFeed', digest.text)
        self.assertNotIn('Open InternshipFeed', format_email([match()], 5, app_base_url='javascript:bad').html)

    def test_singular_subject_and_unicode(self):
        digest = format_email([match(company='研究公司\nTeam')], 1)
        self.assertEqual(digest.subject, 'InternshipFeed — 1 new match')
        self.assertIn('研究公司 Team', digest.text)


class ResendBoundaryTests(unittest.TestCase):
    def test_sdk_payload_receipt_and_stable_idempotency(self):
        digest = format_email([match()], 5)
        with patch('internshipfeed.notifications.resend.Emails.send', return_value={'id': MESSAGE_ID}) as send:
            receipt = send_email(digest, CONFIG)
            send_email(digest, CONFIG)
            self.assertEqual(send.call_args_list[0], send.call_args_list[1])
            params, options = send.call_args.args
        self.assertEqual(params, {'from': CONFIG.from_address, 'to': [CONFIG.to_address], 'subject': digest.subject,
                                 'html': digest.html, 'text': digest.text})
        self.assertTrue(options['idempotency_key'].startswith('internshipfeed-'))
        self.assertEqual(receipt.message_id, MESSAGE_ID)

    def test_errors_are_redacted_and_not_retried(self):
        with patch('internshipfeed.notifications.resend.Emails.send', side_effect=RuntimeError(CONFIG.api_key + CONFIG.to_address)) as send:
            with self.assertRaises(EmailError) as caught:
                send_email(format_email([match()], 5), CONFIG)
        self.assertNotIn(CONFIG.api_key, str(caught.exception))
        self.assertNotIn(CONFIG.to_address, str(caught.exception))
        send.assert_called_once()

    def test_malformed_receipts_and_empty_digest_fail(self):
        for data in ({}, {'id': None}, {'id': 'bad'}, [], None):
            with patch('internshipfeed.notifications.resend.Emails.send', return_value=data), self.assertRaises(EmailError):
                send_email(format_email([match()], 5), CONFIG)
        with patch('internshipfeed.notifications.resend.Emails.send') as send, self.assertRaises(EmailError):
            send_email(format_email([], 5), CONFIG)
        send.assert_not_called()

    def test_config_validation_redacts_values_and_repr(self):
        with patch.dict(os.environ, {}, clear=True), self.assertRaisesRegex(ValueError, 'RESEND_API_KEY'):
            EmailConfig.from_env()
        values = {'RESEND_API_KEY': CONFIG.api_key, 'DAILY_EMAIL_FROM': CONFIG.from_address, 'DAILY_EMAIL_TO': CONFIG.to_address}
        with patch.dict(os.environ, values, clear=True):
            self.assertEqual(EmailConfig.from_env(), CONFIG)
        for invalid in ('private-invalid', 'one@example.com,two@example.com', 'one@example.com\nBcc: other@example.com'):
            with patch.dict(os.environ, {**values, 'DAILY_EMAIL_TO': invalid}, clear=True), self.assertRaises(ValueError) as caught:
                EmailConfig.from_env()
            self.assertNotIn(invalid, str(caught.exception))
        self.assertNotIn(CONFIG.api_key, repr(CONFIG))
        self.assertNotIn(CONFIG.to_address, repr(CONFIG))


class DailyCommandTests(unittest.TestCase):
    def test_defaults_and_environment_validation(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(DailySettings.from_env(), DailySettings(60, 5))
        for values in ({'DAILY_EMAIL_MIN_SCORE': '101'}, {'DAILY_EMAIL_MIN_SCORE': 'bad'}, {'DAILY_EMAIL_TOP_N': '0'}, {'DAILY_EMAIL_TOP_N': '6'}, {'DAILY_NOTIFICATION_CHANNEL': 'sms'}):
            with patch.dict(os.environ, values, clear=True), self.assertRaises(ValueError):
                DailySettings.from_env()

    def test_dry_run_requires_no_credentials_and_prints_subject_and_bodies(self):
        result = dict(status='dry_run', exit_code=0, subject='test subject', message='test digest', html='<p>test digest</p>')
        with patch('internshipfeed.daily_refresh.load_dotenv'), patch.dict(os.environ, {}, clear=True), \
             patch('internshipfeed.daily_refresh.run_daily_refresh', return_value=result) as run, \
             patch('internshipfeed.daily_refresh.EmailConfig.from_env') as config, redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(['--dry-run']), 0)
        config.assert_not_called()
        self.assertTrue(run.call_args.kwargs['dry_run'])
        for value in ('Subject: test subject', 'test digest', '<p>test digest</p>'):
            self.assertIn(value, output.getvalue())

    def test_real_command_requires_config_before_ingestion(self):
        with patch('internshipfeed.daily_refresh.load_dotenv'), patch.dict(os.environ, {}, clear=True), \
             patch('internshipfeed.daily_refresh.run_daily_refresh') as run:
            self.assertEqual(main([]), 2)
        run.assert_not_called()

    def test_retry_flags_paired_and_limits_validated(self):
        for argv in (['--no-ingest'], ['--batch-id', 'not-a-uuid'], ['--enrich-limit', '-1']):
            with self.assertRaises(SystemExit) as caught:
                main(argv)
            self.assertEqual(caught.exception.code, 2)
