"""段落文本提取器。

Paragraph text extraction.
"""

from __future__ import annotations

from pdf_extractor.models import ExtractionResult, Paragraph
from pdf_extractor.rules.rule_schema import ExtractionRule


class TextExtractor:
    """把匹配段落原样返回为提取结果。

    Return matching paragraphs as extraction results.
    """

    def extract(
        self,
        rule: ExtractionRule,
        paragraphs: list[Paragraph],
    ) -> list[ExtractionResult]:
        """为每个候选段落返回一个带坐标的结果。

        Return one traceable result per paragraph.
        """
        # 中文：文本提取不再做二次理解，保持段落原文和段落 bbox。
        # English: Text extraction performs no extra interpretation and keeps paragraph bbox.
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
