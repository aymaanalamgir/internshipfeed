"""Parser for the Canadian Tech Internships Markdown table."""
import html
import re
from urllib.parse import urlparse


def plain(cell):
    cell = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", cell)
    cell = re.sub(r"<br\s*/?>", " / ", cell, flags=re.I)
    return html.unescape(re.sub(r"<[^>]+>|[*`]", "", cell)).strip()


def application_url(cell):
    # Nested badge links contain an image URL followed by the actual apply URL.
    links = re.findall(r"\]\(\s*(https?://[^\s]+?)\s*\)", cell)
    if links:
        return html.unescape(links[-1].strip("<>"))
    match = re.search(r"https?://[^\s<>]+", cell)
    return html.unescape(match.group(0)) if match else None


def parse_readme(markdown):
    records = []
    headers = None
    company = None
    for number, line in enumerate(markdown.splitlines(), 1):
        if not line.strip().startswith("|"):
            if line.lstrip().startswith("#"):
                headers = None
                company = None
            continue
        cells = [c.strip() for c in re.split(r"(?<!\\)\|", line.strip().strip("|"))]
        cells = [c.replace(r"\|", "|") for c in cells]
        normalized = [plain(c).lower() for c in cells]
        if {"company", "role", "location", "apply", "date posted"}.issubset(normalized):
            headers = normalized
            company = None
            continue
        if headers is None or all(re.fullmatch(r":?-+:?", c) for c in cells):
            continue
        if len(cells) != len(headers):
            raise ValueError("Malformed internship row at line %s" % number)
        row = dict(zip(headers, cells))
        name = plain(row["company"])
        if name in {"↳", "↳︎", "↳️"}:
            if company is None:
                raise ValueError("Company continuation without parent at line %s" % number)
            name = company
        else:
            company = name
        role = plain(row["role"])
        location = plain(row["location"])
        if not name or not role or not location:
            raise ValueError("Missing required field at line %s" % number)
        apply = row["apply"]
        url = application_url(apply)
        if url and not urlparse(url).netloc:
            raise ValueError("Invalid application URL at line %s" % number)
        closed = bool(re.search(r"closed|🔒", apply, re.I))
        records.append({
            "company": name, "role": role, "location": location,
            "raw_url": url,
            "status": "closed" if closed else "open" if url else "unknown",
            "date_posted_raw": plain(row["date posted"]),
            "source_line": number, "raw_row": line,
        })
    if not records:
        raise ValueError("No internship rows found; the source format may have changed")
    return records
