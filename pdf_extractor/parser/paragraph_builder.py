"""Paragraph construction from PyMuPDF text blocks."""

from __future__ import annotations

from typing import Any

from pdf_extractor.models import Page, Paragraph, Word
from pdf_extractor.utils.bbox import BBox


class ParagraphBuilder:
    """Convert PyMuPDF text blocks into paragraph models."""

    def build_page(self, pdf_page: Any, page_number: int) -> Page:
        """Build a one-based page model from a PyMuPDF page."""
        paragraphs: list[Paragraph] = []
        words_by_block: dict[int, list[Word]] = {}
        for word in pdf_page.get_text("words", sort=True):
            x0, y0, x1, y1, text, block_number, *_rest = word
            words_by_block.setdefault(int(block_number), []).append(
                Word(
                    text=str(text),
                    bbox=BBox(float(x0), float(y0), float(x1), float(y1)),
                )
            )
        for block in pdf_page.get_text("blocks", sort=True):
            x0, y0, x1, y1, text, block_number, *_rest = block
            normalized_text = " ".join(text.split())
            if not normalized_text:
                continue
            paragraphs.append(
                Paragraph(
                    id=f"p_{page_number:04d}_{len(paragraphs) + 1:04d}",
                    text=normalized_text,
                    page_number=page_number,
                    bbox=BBox(float(x0), float(y0), float(x1), float(y1)),
                    words=words_by_block.get(int(block_number), []),
                )
            )
        return Page(
            page_number=page_number,
            width=float(pdf_page.rect.width),
            height=float(pdf_page.rect.height),
            paragraphs=paragraphs,
        )
