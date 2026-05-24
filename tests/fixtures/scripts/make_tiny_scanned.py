"""Generate tests/fixtures/tiny_scanned.pdf — a synthetic image-only PDF.

Phase 2c §5.6.1 #14: render tiny.pdf to PNGs at 150 DPI, then re-encode the
PNGs as an image-only PDF via PIL.Image.save([...], save_all=True).

Result: a PDF that has page images but NO text layer. The text-layer sniff
will return ~0 chars/page, routing the file through Gemini OCR under the
`auto` strategy.

Run manually (not in CI):
    uv run python tests/fixtures/scripts/make_tiny_scanned.py

Output: tests/fixtures/tiny_scanned.pdf (overwrites if present).
"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image


def main() -> None:
    fixtures_dir = Path(__file__).resolve().parent.parent
    src_pdf = fixtures_dir / "tiny.pdf"
    out_pdf = fixtures_dir / "tiny_scanned.pdf"

    if not src_pdf.exists():
        raise SystemExit(f"missing {src_pdf}")

    # Render each page of tiny.pdf to a PIL image at 150 DPI.
    doc = pdfium.PdfDocument(src_pdf.read_bytes())
    images: list[Image.Image] = []
    for page in doc:
        # scale=2 => roughly 144 DPI (PdfPage.render's `scale=1` is 72 DPI).
        bitmap = page.render(scale=150 / 72)
        pil_img = bitmap.to_pil()
        # Force RGB (drop alpha) so PIL's PDF encoder accepts it.
        if pil_img.mode != "RGB":
            pil_img = pil_img.convert("RGB")
        images.append(pil_img)
    doc.close()

    if not images:
        raise SystemExit("tiny.pdf had zero pages")

    # Encode the images as an image-only PDF.
    buf = BytesIO()
    first, rest = images[0], images[1:]
    first.save(buf, format="PDF", save_all=True, append_images=rest)
    out_pdf.write_bytes(buf.getvalue())

    # Verify: re-open and confirm no text layer.
    verify_doc = pdfium.PdfDocument(out_pdf.read_bytes())
    total_chars = 0
    for page in verify_doc:
        text = page.get_textpage().get_text_range()
        total_chars += len(text)
    verify_doc.close()

    print(f"wrote {out_pdf} ({out_pdf.stat().st_size} bytes, "
          f"{len(images)} pages, total text-layer chars: {total_chars})")


if __name__ == "__main__":
    main()
