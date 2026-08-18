from pathlib import Path

path = Path(
    "/usr/local/lib/python3.14/site-packages/"
    "audiobookdl/sources/nextory.py"
)

text = path.read_text()

old = '''    def get_cover(self, book_info) -> Cover:
        cover_url = self.find_format_data(book_info)["img_url"]
        cover_data = self.get(cover_url)
        return Cover(cover_data, "jpg")
'''

new = '''    def get_cover(self, book_info) -> Cover:
        cover_url = self.find_format_data(book_info)["img_url"]
        cover_data = self.get(cover_url)

        # Nextory returns WebP data even though the URL ends in .jpg.
        # Convert the image to real JPEG bytes before embedding it.
        from io import BytesIO
        from PIL import Image

        image = Image.open(BytesIO(cover_data))
        image = image.convert("RGB")

        output = BytesIO()
        image.save(output, format="JPEG", quality=95)
        cover_data = output.getvalue()

        return Cover(cover_data, "jpg")
'''

if old not in text:
    raise SystemExit(
        "Could not find expected Nextory get_cover() implementation"
    )

path.write_text(text.replace(old, new))

print("Nextory cover patch applied successfully.")
