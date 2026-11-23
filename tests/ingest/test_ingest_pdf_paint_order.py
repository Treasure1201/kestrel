"""PDF filled shapes judged by paint order: behind the text it is a background, over the text it hides it."""

from __future__ import annotations

import pymupdf

from kestrel.ingest import chunk_document, parse_bytes, scan_document

BAND = pymupdf.Rect(70, 185, 400, 210)


def _pdf(*, shape_after: bool, fill=(1, 1, 1), opacity: float = 1.0, text_color=(0, 0, 0), band=BAND) -> bytes:
    pdf = pymupdf.open()
    page = pdf.new_page(width=595, height=842)
    page.insert_text((72, 100), "1. Rates", fontsize=16)
    page.insert_text((72, 140), "Visible opening sentence.", fontsize=11)
    if not shape_after:
        page.draw_rect(band, color=None, fill=fill, fill_opacity=opacity)
    page.insert_text((72, 200), "BANDWORDS fee is R777.", fontsize=11, color=text_color)
    if shape_after:
        page.draw_rect(band, color=None, fill=fill, fill_opacity=opacity, overlay=True)
    page.insert_text((72, 260), "Visible closing sentence.", fontsize=11)
    data = pdf.tobytes()
    pdf.close()
    return data


def _scan(data: bytes):
    document, findings = scan_document(parse_bytes(data, filename="rates.pdf", collection="synthetic"))
    text = "\n".join(s.text for s in document.sections) + "\n".join(c.text for c in chunk_document(document))
    return text, findings


def test_an_opaque_shape_painted_after_the_text_covers_it() -> None:
    for fill in ((1, 1, 1), (0, 0, 0)):  # white box or black box: either way the reader cannot read the text
        text, findings = _scan(_pdf(shape_after=True, fill=fill))
        assert "BANDWORDS" not in text and "R777" not in text
        assert "Visible opening sentence." in text and "Visible closing sentence." in text
        covered = [f for f in findings if f.kind == "covered_text"]
        assert len(covered) == 1 and "BANDWORDS" in covered[0].excerpt and covered[0].locator.startswith("page 1")


def test_a_shape_painted_before_the_text_is_its_background() -> None:
    text, findings = _scan(_pdf(shape_after=False, fill=(0.1, 0.1, 0.3), text_color=(1, 1, 1)))
    assert "BANDWORDS fee is R777." in text and findings == []
    # Dark text on the same dark band painted first is still judged by contrast, not as covered.
    _, findings = _scan(_pdf(shape_after=False, fill=(0.1, 0.1, 0.3), text_color=(0.1, 0.1, 0.3)))
    assert [f.kind for f in findings] == ["low_contrast"]


def test_a_translucent_or_thin_shape_over_text_does_not_cover_it() -> None:
    text, findings = _scan(_pdf(shape_after=True, fill=(1, 1, 0), opacity=0.4))
    assert "BANDWORDS fee is R777." in text and findings == []
    underline = pymupdf.Rect(70, 201, 400, 202)
    text, findings = _scan(_pdf(shape_after=True, fill=(0, 0, 0), band=underline))
    assert "BANDWORDS fee is R777." in text and findings == []
