from pathlib import Path


def test_combine_is_checked_by_default():
    template = (Path(__file__).parents[1] / "app" / "templates" / "download.html").read_text(
        encoding="utf-8"
    )

    assert 'id="combine" name="combine" checked' in template
