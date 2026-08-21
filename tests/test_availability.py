import json

import pytest

from app import availability


def storytel_html(formats: list[str]) -> str:
    payload = {"props": {"pageProps": {"book": {"formats": formats}}}}
    return f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(payload)}</script>'


def test_parse_storytel_ebook_only():
    assert availability.parse_storytel_formats(storytel_html(["EBOOK"])) == {"EBOOK"}


def test_parse_storytel_both_formats():
    assert availability.parse_storytel_formats(storytel_html(["EBOOK", "ABOOK"])) == {
        "EBOOK",
        "ABOOK",
    }


def test_parse_storytel_ignores_unknown_formats():
    assert availability.parse_storytel_formats(storytel_html(["PODCAST", "ABOOK"])) == {"ABOOK"}


@pytest.mark.parametrize(
    "url, expected",
    [
        ("https://www.storytel.com/nl/books/de-dwergen-982640", True),
        ("https://storytel.com/nl/books/example-1", True),
        ("https://evil.example/books/example-1", False),
        ("https://storytel.com.evil.example/nl/books/example-1", False),
        ("https://www.storytel.com/nl/series/example-1", False),
    ],
)
def test_is_storytel_url(url, expected):
    assert availability.is_storytel_url(url) is expected
