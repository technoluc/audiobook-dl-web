from pathlib import Path

MP4_METADATA_PATH = Path(
    "/usr/local/lib/python3.14/site-packages/audiobookdl/output/metadata/mp4.py"
)


def patch_mp4_metadata(source: str) -> str:
    """Make audiobook-dl's M4B tags compatible with Audiobookshelf."""
    replacements = (
        (
            '    "series": "album",\n',
            '    "series": "series",\n',
        ),
        (
            "EasyMP4Tags.RegisterTextKey(\"track\", '\\xa9trk')\n",
            "EasyMP4Tags.RegisterTextKey(\"track\", '\\xa9trk')\n"
            'EasyMP4Tags.RegisterFreeformKey("series", "Series")\n'
            'EasyMP4Tags.RegisterFreeformKey("series_part", "Series-Part")\n',
        ),
        (
            '        elif key == "series_order":\n'
            '            audio["track"] = str(value)\n'
            "        elif key in MP4_CONVERT:\n",
            '        elif key == "series_order":\n'
            '            audio["track"] = str(value)\n'
            '            audio["series_part"] = str(value)\n'
            '        elif key == "title":\n'
            "            # Audiobookshelf treats album as the primary book title.\n"
            '            audio["title"] = value\n'
            '            audio["album"] = value\n'
            "        elif key in MP4_CONVERT:\n",
        ),
    )

    patched = source
    for old, new in replacements:
        if new in patched:
            continue
        if old not in patched:
            raise ValueError(f"Could not find expected audiobook-dl source fragment: {old!r}")
        patched = patched.replace(old, new, 1)
    return patched


def main() -> None:
    source = MP4_METADATA_PATH.read_text(encoding="utf-8")
    patched = patch_mp4_metadata(source)
    MP4_METADATA_PATH.write_text(patched, encoding="utf-8")
    print("Audiobookshelf-compatible M4B metadata patch applied successfully.")


if __name__ == "__main__":
    main()
