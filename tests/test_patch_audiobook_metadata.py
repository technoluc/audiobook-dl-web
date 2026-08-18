import pytest

from patch_audiobook_metadata import patch_mp4_metadata

UPSTREAM_SOURCE = """MP4_CONVERT = {
    "series": "album",
    "title": "title",
}

EasyMP4Tags.RegisterTextKey("track", '\\xa9trk')

def add_mp4_metadata(filepath, metadata):
    for key, value in metadata.all_properties(allow_duplicate_keys=None):
        if key == "release_date":
            pass
        elif key == "series_order":
            audio["track"] = str(value)
        elif key in MP4_CONVERT:
            audio[MP4_CONVERT[key]] = value
"""


def test_patch_writes_book_title_to_album_and_series_to_own_tags():
    patched = patch_mp4_metadata(UPSTREAM_SOURCE)

    assert '"series": "series"' in patched
    assert 'RegisterFreeformKey("series", "Series")' in patched
    assert 'RegisterFreeformKey("series_part", "Series-Part")' in patched
    assert 'audio["series_part"] = str(value)' in patched
    assert 'audio["title"] = value' in patched
    assert 'audio["album"] = value' in patched


def test_patch_is_idempotent():
    patched = patch_mp4_metadata(UPSTREAM_SOURCE)
    assert patch_mp4_metadata(patched) == patched


def test_patch_fails_loudly_when_upstream_layout_changes():
    with pytest.raises(ValueError, match="expected audiobook-dl source fragment"):
        patch_mp4_metadata("unexpected source")
