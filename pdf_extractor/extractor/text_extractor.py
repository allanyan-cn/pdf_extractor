"""Paragraph text extraction."""

from __future__ import annotations

from pdf_extractor.models import ExtractionResult, Paragraph
from pdf_extractor.rules.rule_schema import ExtractionRule


class TextExtractor:
    """Return matching paragraphs as extraction results."""

    def extract(
        self,
        rule: ExtractionRule,
        paragraphs: list[Paragraph],
    ) -> list[ExtractionResult]:
        """Return one traceable result per paragraph."""
        return [
            ExtractionResult(
                rule_id=rule.id,
                rule_name=rule.name,
                extract_type=rule.extract_type,
                target=rule.target,
                value=paragraph.text,
                source_text=paragraph.text,
                page_number=paragraph.page_number,
                bbox=paragraph.bbox,
                paragraph_id=paragraph.id,
                confidence=1.0,
            )
            for paragraph in paragraphs
        ]
