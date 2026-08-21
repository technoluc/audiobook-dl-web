"""Provider-specific checks for the formats available at a book URL."""

import asyncio
import json
from html.parser import HTMLParser
from urllib.parse import urlparse
from urllib.request import Request, urlopen

STORYTEL_HOSTS = {"storytel.com", "www.storytel.com"}
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


def is_storytel_url(url: str) -> bool:
    """Only allow public Storytel book pages to reach the provider checker."""
    parsed = urlparse(url)
    return (
        parsed.scheme in {"http", "https"}
        and (parsed.hostname or "").lower() in STORYTEL_HOSTS
        and "/books/" in parsed.path
    )


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


async def check_url_availability(url: str) -> dict[str, bool]:
    """Return verified audiobook/e-book availability for a supported provider."""
    if not is_storytel_url(url):
        raise ValueError("Availability checking is not supported for this URL")
    formats = await asyncio.to_thread(_fetch_storytel_formats, url)
    return {"audiobook": "ABOOK" in formats, "ebook": "EBOOK" in formats}
