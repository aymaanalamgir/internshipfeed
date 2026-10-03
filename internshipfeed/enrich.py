"""Best-effort extraction from public, server-rendered career pages."""
import json
import re
from html.parser import HTMLParser
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

from .fetch import fetch_text

# Explicit vocabulary: matches come from descriptions, never from role guesses.
VOCABULARY = {
    "languages": ["Python", "JavaScript", "TypeScript", "Java", "C++", "C#", "C", "Go", "Rust", "SQL", "Ruby", "Swift", "Kotlin", "Scala", "MATLAB", "R"],
    "frameworks": ["React", "Next.js", "Angular", "Vue", "Django", "Flask", "FastAPI", "Spring", "Express", ".NET", "PyTorch", "TensorFlow", "scikit-learn"],
    "tools_and_platforms": ["AWS", "Azure", "GCP", "Docker", "Kubernetes", "Git", "Linux", "PostgreSQL", "MySQL", "MongoDB", "Redis", "Kafka", "Spark", "Node.js", "Terraform"],
    "concepts": ["machine learning", "data analysis", "distributed systems", "REST", "GraphQL", "CI/CD", "unit testing", "object-oriented", "agile"],
}


class PageParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.text = []
        self.main = []
        self.scripts = []
        self.script = None
        self.skip = 0
        self.main_depth = 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "script":
            self.script = [] if attrs.get("type", "").lower() == "application/ld+json" else None
        if tag in {"script", "style", "nav", "footer", "header"}:
            self.skip += 1
        if tag in {"main", "article"}:
            self.main_depth += 1
        if tag in {"p", "div", "li", "br", "h1", "h2", "h3", "h4", "section"}:
            self.text.append("\n")
            if self.main_depth:
                self.main.append("\n")
        if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self.text.append("## ")
            if self.main_depth:
                self.main.append("## ")

    def handle_endtag(self, tag):
        if tag == "script" and self.script is not None:
            self.scripts.append("".join(self.script))
            self.script = None
        if tag in {"script", "style", "nav", "footer", "header"}:
            self.skip = max(0, self.skip - 1)
        if tag in {"main", "article"}:
            self.main_depth = max(0, self.main_depth - 1)
        if tag in {"p", "li", "div", "h1", "h2", "h3", "h4"}:
            self.text.append("\n")
            if self.main_depth:
                self.main.append("\n")

    def handle_data(self, data):
        if self.script is not None:
            self.script.append(data)
        if not self.skip:
            self.text.append(data)
            if self.main_depth:
                self.main.append(data)


def clean_text(markup):
    parser = PageParser()
    parser.feed(markup)
    return "\n".join(line.strip() for line in "".join(parser.text).splitlines() if line.strip())


def job_objects(value):
    if isinstance(value, list):
        for item in value:
            yield from job_objects(item)
    elif isinstance(value, dict):
        kind = value.get("@type", [])
        if kind == "JobPosting" or isinstance(kind, list) and "JobPosting" in kind:
            yield value
        for child in value.values():
            if isinstance(child, (dict, list)):
                yield from job_objects(child)


def extract_description(markup):
    parser = PageParser()
    parser.feed(markup)
    for script in parser.scripts:
        try:
            jobs = list(job_objects(json.loads(script)))
        except (ValueError, TypeError):
            continue
        # Multiple JobPostings can be a search page, not a single job description.
        if len(jobs) == 1 and isinstance(jobs[0].get("description"), str):
            return clean_text(jobs[0]["description"]), "json_ld"
    content = "".join(parser.main)
    text = clean_text(content)
    # Require job-specific sections; do not treat a generic JS shell as a description.
    if len(text) >= 200 and re.search(r"qualifications|requirements|responsibilities|what you.ll|about the role", text, re.I):
        return text, "html_main"
    return None, None


def extract_keywords(description):
    keywords = {}
    for category, terms in VOCABULARY.items():
        matches = []
        for term in terms:
            # Special punctuation boundaries prevent Java matching JavaScript, C matching C++.
            pattern = r"(?<![\w+#])" + re.escape(term) + r"(?![\w+#])"
            if term == "R":
                pattern += r"(?![&/])"
            flags = 0 if term in {"C", "R", "Go"} else re.I
            if re.search(pattern, description, flags):
                matches.append(term)
        keywords[category] = matches
    requirements = []
    in_section = False
    heading_terms = r"qualifications|requirements|what you bring|what you.ll bring|what we.re looking for|who you are|what you have|you may be a good fit"
    for line in description.splitlines():
        is_heading = line.startswith("## ")
        if is_heading:
            in_section = bool(re.search(heading_terms, line, re.I))
            continue
        if len(line) < 100 and re.search(heading_terms, line, re.I):
            in_section = True
            continue
        if len(line) < 100 and re.search(r"responsibilities|benefits|compensation|what you.ll do|about us|equal opportunity|what we offer|hiring process", line, re.I):
            in_section = False
        if in_section and len(line) > 20:
            requirements.append(line)
    keywords["requirements"] = requirements
    return keywords


def enrich_job(job, robots_cache=None):
    result = {"status": "skipped", "description": None, "keywords": None, "error": None}
    if job.status in {"closed", "dead"} or not job.url:
        result["error"] = "Closed listing or no application URL"
        return result
    cache = robots_cache if robots_cache is not None else {}
    try:
        parsed = urlparse(job.url)
        origin = "%s://%s" % (parsed.scheme, parsed.netloc)
        if origin not in cache:
            from urllib.error import HTTPError
            try:
                robots, _ = fetch_text(origin + "/robots.txt", retries=0)
                policy = RobotFileParser()
                policy.parse(robots.splitlines())
                cache[origin] = policy
            except HTTPError as exc:
                if exc.code == 404:
                    cache[origin] = None
                else:
                    raise
        policy = cache[origin]
        if policy and not policy.can_fetch("InternshipFeed", job.url):
            result["error"] = "Disallowed by robots.txt"
            return result
        from urllib.error import HTTPError
        try:
            markup, resolved_url = fetch_text(job.url)
        except HTTPError as exc:
            if exc.code in {404, 410}:
                result.update(status="unavailable", dead=True, error="Application link returned HTTP %s" % exc.code)
                return result
            raise
        description, method = extract_description(markup)
        result["resolved_url"] = resolved_url
        if not description:
            result.update(status="unavailable", error="No readable job description; page may require JavaScript or an ATS adapter")
            return result
        result.update(status="success", description=description, keywords=extract_keywords(description), extraction_method=method)
    except Exception as exc:
        result.update(status="failed", error="%s: %s" % (type(exc).__name__, exc))
    return result
