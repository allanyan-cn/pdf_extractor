"""从 PyMuPDF 文本块构建段落模型。

Paragraph construction from PyMuPDF text blocks.
"""

from __future__ import annotations

from typing import Any

from pdf_extractor.models import Page, Paragraph, Word
from pdf_extractor.utils.bbox import BBox


class ParagraphBuilder:
    """将 PyMuPDF 文本块转换为段落模型。

    Convert PyMuPDF text blocks into paragraph models.
    """

    def build_page(self, pdf_page: Any, page_number: int) -> Page:
        """从 PyMuPDF 页面构建 1-based 页码的 Page 模型。

        Build a one-based page model from a PyMuPDF page.
        """
        paragraphs: list[Paragraph] = []
        words_by_block: dict[int, list[Word]] = {}
        # 中文：先按 block 收集 word 坐标，后续数值 span bbox 会优先使用这些坐标。
        # English: Collect word coordinates by block for precise value span bboxes later.
        for word in pdf_page.get_text("words", sort=True):
            x0, y0, x1, y1, text, block_number, *_rest = word
            words_by_block.setdefault(int(block_number), []).append(
                Word(
                    text=str(text),
                    bbox=BBox(float(x0), float(y0), float(x1), float(y1)),
                )
            )
        # 中文：第一版把 PDF text block 视为段落，避免过早引入复杂段落合并逻辑。
        # English: V1 treats each PDF text block as a paragraph to keep parsing simple.
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
