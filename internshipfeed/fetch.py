"""HTTP fetching, independent of source parsing."""
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

def fetch_text(url, timeout=20, retries=2):
    if urlparse(url).scheme not in {"http", "https"}:
        raise ValueError("Only HTTP(S) URLs are supported")
    for attempt in range(retries + 1):
        try:
            request = Request(url, headers={
                "User-Agent": "InternshipFeed/0.1",
                "Accept": "text/html,application/json,text/plain;q=0.9",
            })
            with urlopen(request, timeout=timeout) as response:
                data = response.read(5_000_001)
                if len(data) > 5_000_000:
                    raise ValueError("Response exceeds 5 MB limit")
                charset = response.headers.get_content_charset() or "utf-8"
                return data.decode(charset, errors="replace"), response.geturl()
        except HTTPError as exc:
            if exc.code not in {429, 500, 502, 503, 504} or attempt == retries:
                raise
        except (URLError, TimeoutError):
            if attempt == retries:
                raise
        time.sleep(2 ** attempt)
