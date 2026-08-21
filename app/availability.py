"""Provider-specific checks for the formats available at a book URL."""

import asyncio
import json
from html.parser import HTMLParser
from urllib.parse import urlparse
from urllib.request import Request, urlopen

STORYTEL_HOSTS = {"storytel.com", "www.storytel.com"}
NEXTORY_HOSTS = {"nextory.com", "www.nextory.com"}
MAX_RESPONSE_BYTES = 4 * 1024 * 1024


class _NextDataParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.in_next_data = False
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "script" and dict(attrs).get("id") == "__NEXT_DATA__":
            self.in_next_data = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "script":
            self.in_next_data = False

    def handle_data(self, data: str) -> None:
        if self.in_next_data:
            self.parts.append(data)


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


def is_storytel_url(url: str) -> bool:
    """Only allow public Storytel book pages to reach the provider checker."""
    parsed = urlparse(url)
    return (
        parsed.scheme in {"http", "https"}
        and (parsed.hostname or "").lower() in STORYTEL_HOSTS
        and "/books/" in parsed.path
    )


def is_nextory_url(url: str) -> bool:
    """Only allow public Nextory book pages to reach the provider checker."""
    parsed = urlparse(url)
    return (
        parsed.scheme in {"http", "https"}
        and (parsed.hostname or "").lower() in NEXTORY_HOSTS
        and "/book/" in parsed.path
    )


def availability_provider(url: str) -> str | None:
    if is_storytel_url(url):
        return "storytel"
    if is_nextory_url(url):
        return "nextory"
    return None


def parse_storytel_formats(html: str) -> set[str]:
    """Extract the current title's formats, excluding formats from recommendations."""
    parser = _NextDataParser()
    parser.feed(html)
    if not parser.parts:
        raise ValueError("Storytel page does not contain __NEXT_DATA__")

    payload = json.loads("".join(parser.parts))
    formats = payload["props"]["pageProps"]["book"]["formats"]
    if not isinstance(formats, list):
        raise ValueError("Storytel returned an invalid formats value")
    return {item for item in formats if item in {"ABOOK", "EBOOK"}}


def _fetch_storytel_formats(url: str) -> set[str]:
    if not is_storytel_url(url):
        raise ValueError("Not a supported Storytel book URL")

    request = Request(url, headers={"User-Agent": "audiobook-dl-web/1.0"})
    with urlopen(request, timeout=10) as response:  # noqa: S310 - hostname is allowlisted above
        if not is_storytel_url(response.geturl()):
            raise ValueError("Storytel redirected to an unsupported URL")
        body = response.read(MAX_RESPONSE_BYTES + 1)
        if len(body) > MAX_RESPONSE_BYTES:
            raise ValueError("Storytel response is too large")
        charset = response.headers.get_content_charset() or "utf-8"
    return parse_storytel_formats(body.decode(charset, errors="replace"))


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
                book_format = str(edition.get("bookFormat", ""))
                if edition.get("@type") == "Audiobook" or book_format.endswith("/AudiobookFormat"):
                    formats.add("ABOOK")
                if book_format.endswith("/EBook"):
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
    """Return verified audiobook/e-book availability for a supported provider."""
    provider = availability_provider(url)
    if provider == "storytel":
        formats = await asyncio.to_thread(_fetch_storytel_formats, url)
    elif provider == "nextory":
        formats = await asyncio.to_thread(_fetch_nextory_formats, url)
    else:
        raise ValueError("Availability checking is not supported for this URL")
    return {"audiobook": "ABOOK" in formats, "ebook": "EBOOK" in formats}
