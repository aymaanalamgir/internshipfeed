import unittest
from io import BytesIO
from unittest.mock import MagicMock, patch

from fastapi import HTTPException, UploadFile
from starlette.requests import Request

from internshipfeed.api import upload_resume
from internshipfeed.resumes import MAX_PDF_PAGES, MAX_UPLOAD_BYTES, ResumeExtractionError, extract_pdf_text
from pdf_fixtures import make_pdf


class PdfExtractionTests(unittest.TestCase):
    def test_text_and_technical_symbols_are_preserved(self):
        self.assertEqual(extract_pdf_text(make_pdf(['Sample Candidate\nC++ / C# / .NET\nBackend Engineering'])),
                         'Sample Candidate\nC++ / C# / .NET\nBackend Engineering')

    def test_multiple_pages_preserve_document_order(self):
        self.assertEqual(extract_pdf_text(make_pdf(['Education', 'Experience', 'Projects'])),
                         'Education\n\nExperience\n\nProjects')

    def test_blank_page_does_not_discard_other_pages(self):
        self.assertEqual(extract_pdf_text(make_pdf(['Education', None, 'Projects'])), 'Education\n\nProjects')

    def test_none_page_and_conservative_cleanup(self):
        with patch('internshipfeed.resumes.pdfplumber.open') as open_pdf:
            pdf = open_pdf.return_value.__enter__.return_value
            pdf.pages = [MagicMock(), MagicMock(), MagicMock()]
            for page, text in zip(pdf.pages, ['  Education\r\n  C++ / C#  \r\n\r\n\r\n', None, 'Projects\x00  ']):
                page.extract_text.return_value = text
            self.assertEqual(extract_pdf_text(b'fixture'), 'Education\n  C++ / C#\n\nProjects')
            for page in pdf.pages:
                page.extract_text.assert_called_once_with()
            open_pdf.return_value.__exit__.assert_called_once()

    def test_invalid_or_corrupt_pdf_fails_without_document_details(self):
        for content in (b'private resume content, not a PDF', b'%PDF-1.4\ncorrupt', b''):
            with self.subTest(content=content), self.assertRaisesRegex(ResumeExtractionError, '^Could not read this PDF'):
                extract_pdf_text(content)

    def test_empty_pdf_is_rejected(self):
        with self.assertRaisesRegex(ResumeExtractionError, 'Could not extract readable text'):
            extract_pdf_text(make_pdf([None, None]))

    def test_ten_pages_are_accepted(self):
        text = extract_pdf_text(make_pdf(['Page %s' % index for index in range(MAX_PDF_PAGES)]))
        self.assertTrue(text.startswith('Page 0'))
        self.assertTrue(text.endswith('Page 9'))

    def test_page_limit_is_checked_before_text_extraction(self):
        with patch('internshipfeed.resumes.pdfplumber.open') as open_pdf:
            pages = [MagicMock() for _ in range(MAX_PDF_PAGES + 1)]
            open_pdf.return_value.__enter__.return_value.pages = pages
            with self.assertRaisesRegex(ResumeExtractionError, 'at most 10 pages'):
                extract_pdf_text(b'fixture')
            for page in pages:
                page.extract_text.assert_not_called()
            open_pdf.return_value.__exit__.assert_called_once()
        with self.assertRaisesRegex(ResumeExtractionError, 'at most 10 pages'):
            extract_pdf_text(make_pdf(['text'] * (MAX_PDF_PAGES + 1)))

    def test_oversized_content_is_rejected_before_pdf_is_opened(self):
        with patch('internshipfeed.resumes.pdfplumber.open') as open_pdf:
            with self.assertRaisesRegex(ResumeExtractionError, '5 MiB'):
                extract_pdf_text(b'x' * (MAX_UPLOAD_BYTES + 1))
            open_pdf.assert_not_called()


class UploadResourceTests(unittest.TestCase):
    def test_unknown_size_upload_uses_bounded_read_and_closes_on_rejection(self):
        stream = BytesIO(b'x' * (MAX_UPLOAD_BYTES + 2))
        upload = UploadFile(file=stream, filename='resume.pdf', size=None)
        request = Request({'type': 'http', 'client': ('127.0.0.1', 1), 'headers': []})
        with patch.object(stream, 'read', wraps=stream.read) as read, patch('internshipfeed.api.extract_pdf_text') as extract:
            with self.assertRaises(HTTPException) as error:
                upload_resume(request=request, file=upload, conn=object())
            self.assertEqual(error.exception.status_code, 413)
            read.assert_called_once_with(MAX_UPLOAD_BYTES + 1)
            extract.assert_not_called()
        self.assertTrue(stream.closed)

    def test_upload_closes_after_success_and_parser_failure(self):
        request = Request({'type': 'http', 'client': ('127.0.0.1', 1), 'headers': []})
        for content in (make_pdf(['Sample Candidate']), b'private invalid content'):
            upload = UploadFile(file=BytesIO(content), filename='resume.pdf')
            with patch('internshipfeed.api.db.development_profile', return_value={'id': 1}), patch('internshipfeed.api.db.save_resume_text') as save:
                if content.startswith(b'%PDF'):
                    upload_resume(request=request, file=upload, conn=object())
                    save.assert_called_once_with(unittest.mock.ANY, 1, 'resume.pdf', 'Sample Candidate')
                else:
                    with self.assertRaises(HTTPException) as error:
                        upload_resume(request=request, file=upload, conn=object())
                    self.assertEqual(error.exception.status_code, 422)
                    save.assert_not_called()
            self.assertTrue(upload.file.closed)
