"""Fake PDF invoices (GENERATE-FAKE-TESTING-EMAILS.md). Needs the `eval` extra (reportlab,
Pillow). Deterministic: reportlab's invariant mode and seeded noise images, so rebuilds are
byte-identical. No JavaScript, forms, embedded files or links.
"""

from __future__ import annotations

import io
import math
import random

from ecf.errors import ServiceUnavailableError
from ecf.eval.cards import Attachment

MB = 1024 * 1024
BASE64_GROWTH = 4 / 3 * 78 / 76  # base64 plus CRLF every 76 characters
SIZE_TOLERANCE = 0.02


def invoice_pdf(att: Attachment, seed: str) -> bytes:
    import importlib.util  # noqa: PLC0415

    if not all(importlib.util.find_spec(m) for m in ("PIL", "reportlab")):
        raise ServiceUnavailableError(
            "fake PDFs need the eval extra: uv sync (dev) or install email-classify-filter[eval]"
        )

    noise_side = 0
    target_pdf = 0.0
    if att.scanned and att.target_eml_mb:
        target_pdf = att.target_eml_mb * MB / BASE64_GROWTH
        noise_side = int(math.sqrt(target_pdf / att.pages / 3))
    data = _render(att, seed, noise_side)
    # Encoding overhead inside the PDF isn't known in advance: measure and rescale (deterministic).
    for _ in range(3):
        if not target_pdf or abs(len(data) - target_pdf) / target_pdf < SIZE_TOLERANCE:
            break
        noise_side = int(noise_side * math.sqrt(target_pdf / len(data)))
        data = _render(att, seed, noise_side)
    return data


def _render(att: Attachment, seed: str, noise_side: int) -> bytes:
    from PIL import Image  # noqa: PLC0415
    from reportlab.lib.pagesizes import letter  # noqa: PLC0415
    from reportlab.lib.utils import ImageReader  # noqa: PLC0415
    from reportlab.pdfgen import canvas  # noqa: PLC0415

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter, invariant=True, pageCompression=1)
    c.setAuthor("ecf synthetic test set")
    c.setTitle(f"Invoice {att.invoice.number}")
    width, height = letter
    rng = random.Random(seed)  # noqa: S311 - deterministic test data, not security
    for page in range(1, att.pages + 1):
        if noise_side:
            data = rng.randbytes(noise_side * noise_side * 3)
            img = Image.frombytes("RGB", (noise_side, noise_side), data)
            c.drawImage(ImageReader(img), 0, 0, width=width, height=height)  # pyright: ignore[reportUnknownMemberType]
        _invoice_text(c, att, page, height)
        c.showPage()
    c.save()
    return buf.getvalue()


def _invoice_text(c: object, att: Attachment, page: int, height: float) -> None:
    from reportlab.pdfgen.canvas import Canvas  # noqa: PLC0415

    assert isinstance(c, Canvas)  # noqa: S101 - narrow for the type checker
    inv = att.invoice
    y = height - 72
    lines = [
        "SYNTHETIC TEST DOCUMENT - NOT A REAL INVOICE",
        f"{att.vendor}",
        f"Invoice {inv.number}    Due {inv.due}    Page {page} of {att.pages}",
        "",
        *[f"{desc:<40} {amount:>12,.2f}" for desc, amount in inv.lines],
        f"{'Total':<40} {sum(a for _, a in inv.lines):>12,.2f}",
        "",
        f"Remit to: {inv.bank}",
    ]
    c.setFont("Courier", 11)
    for line in lines:
        c.drawString(72, y, line)
        y -= 16
