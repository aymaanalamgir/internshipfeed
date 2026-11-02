"""Local PDF text extraction, without candidate interpretation or file retention."""
import re
from io import BytesIO

import pdfplumber
from pdfminer.psparser import PSException
from pdfplumber.utils.exceptions import MalformedPDFException, PdfminerException

MAX_UPLOAD_BYTES = 5 * 1024 * 1024
MAX_PDF_PAGES = 10


class ResumeExtractionError(Exception):
    """An expected document failure whose message is safe to return to clients."""


def extract_pdf_text(content: bytes) -> str:
    """Extract pages in order and reject unreadable, oversized, or lengthy PDFs."""
    if len(content) > MAX_UPLOAD_BYTES:
        raise ResumeExtractionError("PDF must be 5 MiB or smaller.")
    try:
        with BytesIO(content) as stream, pdfplumber.open(stream) as pdf:
            if len(pdf.pages) > MAX_PDF_PAGES:
                raise ResumeExtractionError("PDF must contain at most 10 pages.")
            pages = [page.extract_text() or "" for page in pdf.pages]
    except (PdfminerException, MalformedPDFException, PSException, ValueError):
        # Parser diagnostics can contain document content; expose only this message.
        raise ResumeExtractionError("Could not read this PDF. Use a valid, unencrypted PDF.") from None

    text = "\n\n".join(pages).replace("\r\n", "\n").replace("\r", "\n")
    # NUL is not readable text and cannot be stored in PostgreSQL text columns.
    text = text.replace("\x00", "")
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if not text:
        raise ResumeExtractionError("Could not extract readable text from this PDF.")
    return text
