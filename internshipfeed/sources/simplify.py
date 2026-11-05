"""Parse Simplify's category HTML tables, including badges and continuation rows."""
import re
import hashlib
from html.parser import HTMLParser
from urllib.parse import urlparse

from ..records import SourceJobRecord


def text_content(markup, ignore_summary=False):
    class Text(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.parts = []
            self.summary = 0

        def handle_starttag(self, tag, attrs):
            if tag == "summary" and ignore_summary:
                self.summary += 1
            if tag == "br":
                self.parts.append(" / ")

        def handle_endtag(self, tag):
            if tag == "summary" and ignore_summary:
                self.summary = max(0, self.summary - 1)

        def handle_data(self, value):
            if not self.summary:
                self.parts.append(value)

    parser = Text()
    parser.feed(markup)
    return re.sub(r"\s+", " ", "".join(parser.parts)).strip()


def links(markup):
    class Links(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.hrefs = []

        def handle_starttag(self, tag, attrs):
            value = dict(attrs).get("href", "")
            if tag == "a" and urlparse(value).scheme in {"http", "https"}:
                self.hrefs.append(value)
    parser = Links()
    parser.feed(markup)
    return parser.hrefs


class Tables(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.depth = 0
        self.tables = []
        self.rows = None
        self.row = None
        self.cell = None
        self.line = None

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self.depth += 1
            if self.depth == 1:
                self.rows = []
        if self.depth != 1:
            return
        if tag == "tr":
            self.row = []
            self.line = self.getpos()[0]
        elif tag in {"td", "th"}:
            self.cell = []
        elif self.cell is not None:
            self.cell.append(self.get_starttag_text())

    def handle_startendtag(self, tag, attrs):
        if self.cell is not None:
            self.cell.append(self.get_starttag_text())

    def handle_endtag(self, tag):
        if self.depth == 1:
            if tag in {"td", "th"} and self.cell is not None:
                self.row.append("".join(self.cell))
                self.cell = None
            elif tag == "tr" and self.row is not None:
                self.rows.append((self.line, self.row))
                self.row = None
            elif self.cell is not None:
                self.cell.append("</%s>" % tag)
        if tag == "table":
            if self.depth == 1:
                self.tables.append(self.rows)
            self.depth = max(0, self.depth - 1)

    def handle_data(self, value):
        if self.cell is not None:
            self.cell.append(value)

    def handle_entityref(self, name):
        self.handle_data("&%s;" % name)

    def handle_charref(self, name):
        self.handle_data("&#%s;" % name)


def parse_simplify(markdown):
    parser = Tables()
    parser.feed(markdown)
    if parser.depth or parser.cell is not None or parser.row is not None:
        raise ValueError("Incomplete Simplify HTML table")
    heading = re.search(r"^\s*#\s+.*?\b(Summer|Winter|Fall|Spring)\s+(20\d{2})\b", markdown, re.M | re.I)
    default_term = "%s %s" % (heading[1].title(), heading[2]) if heading else None
    lines = markdown.splitlines()
    records = []
    for table in parser.tables:
        headers = None
        company = None
        for number, cells in table:
            names = [text_content(cell).casefold() for cell in cells]
            if {"company", "role", "location"}.issubset(names) and ("application" in names or "apply" in names):
                headers = names
                company = None
                continue
            if headers is None:
                continue
            row_headers = headers
            # Archived off-season tables omit the Term heading but retain its cells.
            if (len(cells) == len(headers) + 1 and headers[:3] == ['company', 'role', 'location']
                    and 'term' not in headers and re.fullmatch(r'(?:Winter|Spring|Summer|Fall|Autumn)\s+20\d{2}(?:,\s*(?:Winter|Spring|Summer|Fall|Autumn)\s+20\d{2})*', text_content(cells[3]), re.I)):
                row_headers = headers[:3] + ['term'] + headers[3:]
            if len(cells) != len(row_headers):
                raise ValueError("Malformed Simplify row at line %s" % number)
            row = dict(zip(row_headers, cells))
            name = text_content(row["company"])
            name = re.sub(r"[🔥🛂🎓🇺🇸]", "", name).replace("\ufe0f", "").strip()
            if name in {"↳", "↪", ""}:
                if not company:
                    raise ValueError("Simplify continuation without company at line %s" % number)
                name = company
            else:
                company = name
            title = text_content(row["role"])
            location = text_content(row["location"], ignore_summary=True)
            application = row.get("application", row.get("apply", ""))
            urls = links(application)
            # Prefer the employer's actual application URL over the Simplify companion link.
            direct = [url for url in urls if urlparse(url).hostname not in {"simplify.jobs", "www.simplify.jobs"}]
            url = (direct or urls or [None])[0]
            closed = "🔒" in text_content(application) or bool(re.search(r"\bclosed\b", text_content(application), re.I))
            companion = next((url for url in urls if urlparse(url).hostname in {"simplify.jobs", "www.simplify.jobs"} and urlparse(url).path.startswith("/p/")), None)
            external_id = "simplify:" + urlparse(companion).path.rstrip("/").split("/")[-1] if companion else None
            row_term = text_content(row.get('term', ''))
            if external_id is None and row_term:
                identity = '\0'.join(value.casefold() for value in (name, title, location, row_term))
                external_id = 'simplify:row:' + hashlib.sha256(identity.encode()).hexdigest()
            raw_date = text_content(row.get("age", row.get("date posted", row.get("date", ""))))
            kind = "relative" if re.fullmatch(r"\d+\s*[dhwm]", raw_date, re.I) else "exact" if re.search(r"\b20\d{2}\b", raw_date) else "yearless" if raw_date else "missing"
            if not name or not title or not location:
                raise ValueError("Missing Simplify listing field at line %s" % number)
            records.append(SourceJobRecord(
                company=name, title=title, location=location, url=url,
                status="closed" if closed else "open" if url else "unknown",
                posted_at_raw=raw_date, date_kind=kind, term=row_term or default_term,
                external_id=external_id, source_line=number,
                raw_company=row["company"], raw_title=row["role"], raw_location=row["location"],
                raw_url=url, raw_posted_at=raw_date,
                raw_row="\n".join(lines[number - 1:number]) + "\n" + " | ".join(cells),
            ))
    if not records:
        raise ValueError("No Simplify internship rows found; the source format may have changed")
    return records
