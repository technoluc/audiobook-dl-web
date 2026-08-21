"""Provider-specific checks for the formats available at a book URL."""

import asyncio
import json
from html.parser import HTMLParser
from urllib.parse import urlparse
from urllib.request import Request, urlopen

NEXTORY_HOSTS = {"nextory.com", "www.nextory.com"}
MAX_RESPONSE_BYTES = 4 * 1024 * 1024


class _JsonLdParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.in_json_ld = False
        self.current_parts: list[str] = []
        self.documents: list[dict] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "script" and dict(attrs).get("type") == "application/ld+json":
            self.in_json_ld = True
            self.current_parts = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self.in_json_ld:
            self.in_json_ld = False
            payload = json.loads("".join(self.current_parts))
            if isinstance(payload, dict):
                self.documents.append(payload)

    def handle_data(self, data: str) -> None:
        if self.in_json_ld:
            self.current_parts.append(data)


def is_nextory_url(url: str) -> bool:
    """Only allow public Nextory book pages to reach the provider checker."""
    parsed = urlparse(url)
    return (
        parsed.scheme in {"http", "https"}
        and (parsed.hostname or "").lower() in NEXTORY_HOSTS
        and "/book/" in parsed.path
    )


def parse_nextory_formats(html: str) -> set[str]:
    """Extract formats for the current title from Nextory's schema.org metadata."""
    parser = _JsonLdParser()
    parser.feed(html)

    for document in parser.documents:
        graph = document.get("@graph", [document])
        if not isinstance(graph, list):
            continue
        for item in graph:
            if not isinstance(item, dict) or item.get("@type") != "Book":
                continue
            editions = item.get("workExample")
            if not isinstance(editions, list):
                continue
            formats = set()
            for edition in editions:
                if not isinstance(edition, dict):
                    continue
                if edition.get("@type") == "Audiobook" or str(
                    edition.get("bookFormat", "")
                ).endswith("/AudiobookFormat"):
                    formats.add("ABOOK")
                if str(edition.get("bookFormat", "")).endswith("/EBook"):
                    formats.add("EBOOK")
            return formats

    raise ValueError("Nextory page does not contain book format metadata")


def _fetch_nextory_formats(url: str) -> set[str]:
    if not is_nextory_url(url):
        raise ValueError("Not a supported Nextory book URL")

    request = Request(url, headers={"User-Agent": "audiobook-dl-web/1.0"})
    with urlopen(request, timeout=10) as response:  # noqa: S310 - hostname is allowlisted above
        if not is_nextory_url(response.geturl()):
            raise ValueError("Nextory redirected to an unsupported URL")
        body = response.read(MAX_RESPONSE_BYTES + 1)
        if len(body) > MAX_RESPONSE_BYTES:
            raise ValueError("Nextory response is too large")
        charset = response.headers.get_content_charset() or "utf-8"
    return parse_nextory_formats(body.decode(charset, errors="replace"))


async def check_url_availability(url: str) -> dict[str, bool]:
    """Return verified audiobook/e-book availability for a Nextory title."""
    if not is_nextory_url(url):
        raise ValueError("Availability checking is not supported for this URL")
    formats = await asyncio.to_thread(_fetch_nextory_formats, url)
    return {"audiobook": "ABOOK" in formats, "ebook": "EBOOK" in formats}
