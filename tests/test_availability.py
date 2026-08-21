import json

import pytest

from app import availability


def nextory_html(editions: list[dict]) -> str:
    payload = {
        "@context": "https://schema.org",
        "@graph": [{"@type": "Book", "name": "Example", "workExample": editions}],
    }
    return f'<script type="application/ld+json">{json.dumps(payload)}</script>'


def test_parse_nextory_audiobook_only():
    html = nextory_html(
        [{"@type": "Audiobook", "bookFormat": "https://schema.org/AudiobookFormat"}]
    )
    assert availability.parse_nextory_formats(html) == {"ABOOK"}


def test_parse_nextory_ebook_only():
    html = nextory_html([{"@type": "Book", "bookFormat": "https://schema.org/EBook"}])
    assert availability.parse_nextory_formats(html) == {"EBOOK"}


def test_parse_nextory_both_formats():
    html = nextory_html(
        [
            {"@type": "Audiobook", "bookFormat": "https://schema.org/AudiobookFormat"},
            {"@type": "Book", "bookFormat": "https://schema.org/EBook"},
        ]
    )
    assert availability.parse_nextory_formats(html) == {"ABOOK", "EBOOK"}


@pytest.mark.parametrize(
    "url, expected",
    [
        ("https://nextory.com/nl/book/example-123", True),
        ("https://www.nextory.com/nl/book/example-123", True),
        ("https://evil.example/nl/book/example-123", False),
        ("https://nextory.com.evil.example/nl/book/example-123", False),
        ("https://nextory.com/nl/series/example-123", False),
    ],
)
def test_is_nextory_url(url, expected):
    assert availability.is_nextory_url(url) is expected
