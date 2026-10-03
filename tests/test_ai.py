import json
import unittest
from io import BytesIO
from unittest.mock import patch
from urllib.error import HTTPError

from internshipfeed.ai import DescriptionAnalysis, DescriptionTransformer, validate_evidence

DESCRIPTION = 'Requirements: You must be enrolled in Computer Science. Experience with Python and React is preferred. Responsibilities: Build internal tools.'
ANALYSIS = {
    'overview': 'Build internal tools while studying Computer Science. Python and React are preferred.',
    'key_requirements': [{'text': 'Enrolled in Computer Science', 'evidence': 'You must be enrolled in Computer Science.'}],
    'preferred_qualifications': [{'text': 'Python and React experience', 'evidence': 'Experience with Python and React is preferred.'}],
    'tech_stack': [{'text': 'Python', 'evidence': 'Python'}],
    'frameworks': [{'text': 'React', 'evidence': 'React'}],
    'responsibilities': [{'text': 'Build internal tools', 'evidence': 'Build internal tools.'}],
}


class AiTests(unittest.TestCase):
    def transformer(self, configured=True):
        with patch('internshipfeed.ai.settings', return_value={'configured': configured, 'model': 'gemini-2.5-flash-lite'}), patch.dict('os.environ', {'GEMINI_API_KEY': 'test-google-key'}):
            return DescriptionTransformer()

    def test_gemini_response_validated_and_key_not_in_url(self):
        raw = {'candidates': [{'finishReason': 'STOP', 'content': {'parts': [{'text': json.dumps(ANALYSIS)}]}}]}
        with patch('internshipfeed.ai.urlopen', return_value=BytesIO(json.dumps(raw).encode())) as request:
            result = self.transformer().transform(DESCRIPTION)
        self.assertEqual(result['status'], 'success')
        self.assertEqual(result['analysis'].frameworks[0].text, 'React')
        req = request.call_args.args[0]
        self.assertNotIn('test-google-key', req.full_url)
        self.assertEqual(req.get_header('X-goog-api-key'), 'test-google-key')
        body = json.loads(req.data)
        self.assertEqual(body['contents'][0]['parts'][0]['text'], DESCRIPTION)
        self.assertIn('responseJsonSchema', body['generationConfig'])

    def test_missing_key_never_calls_google(self):
        with patch('internshipfeed.ai.urlopen') as request:
            result = self.transformer(False).transform(DESCRIPTION)
        self.assertEqual(result['status'], 'not_configured')
        request.assert_not_called()

    def test_quota_stops_further_requests_without_paid_fallback(self):
        transformer = self.transformer()
        error = HTTPError('https://generativelanguage.googleapis.com', 429, 'quota', {}, None)
        with patch('internshipfeed.ai.urlopen', side_effect=error) as request:
            first = transformer.transform(DESCRIPTION)
            second = transformer.transform(DESCRIPTION)
        self.assertEqual(first['status'], 'failed')
        self.assertTrue(second['deferred'])
        self.assertEqual(request.call_count, 1)

    def test_unsupported_evidence_rejected(self):
        bad = dict(ANALYSIS, tech_stack=[{'text': 'Kubernetes', 'evidence': 'Kubernetes expertise required'}])
        with self.assertRaises(ValueError):
            validate_evidence(DescriptionAnalysis.model_validate(bad), DESCRIPTION)

    def test_blocked_response_not_saved(self):
        raw = {'candidates': [{'finishReason': 'SAFETY'}]}
        with patch('internshipfeed.ai.urlopen', return_value=BytesIO(json.dumps(raw).encode())):
            result = self.transformer().transform(DESCRIPTION)
        self.assertEqual(result['status'], 'failed')
        self.assertNotIn('analysis', result)
