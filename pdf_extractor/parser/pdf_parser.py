"""PDF 解析编排入口。

PDF parsing orchestration.
"""

from __future__ import annotations

from pathlib import Path

import pymupdf

from pdf_extractor.models import Document
from pdf_extractor.parser.paragraph_builder import ParagraphBuilder
from pdf_extractor.parser.section_detector import SectionDetector


class PDFParser:
    """将文本型 PDF 解析为内部文档模型。

    Parse text-based PDF files into document models.
    """

    def __init__(
        self,
        paragraph_builder: ParagraphBuilder | None = None,
        section_detector: SectionDetector | None = None,
    ) -> None:
        """初始化解析器及可替换的段落/章节组件。

        Initialize the parser and replaceable paragraph/section components.
        """
        self.paragraph_builder = paragraph_builder or ParagraphBuilder()
        self.section_detector = section_detector or SectionDetector()

    def parse(self, file_path: str) -> Document:
        """解析 PDF，并拒绝没有可提取文本的文件。

        Parse a PDF and reject files without extractable text.
        """
        path = Path(file_path)
        if not path.is_file():
            raise FileNotFoundError(f"PDF file does not exist: {file_path}")
        with pymupdf.open(path) as pdf_doc:
            # 中文：先按页构建段落和 word 坐标，再基于整本文档识别章节。
            # English: Build page paragraphs/word coordinates before detecting sections.
            pages = [
                self.paragraph_builder.build_page(pdf_page, page_number)
                for page_number, pdf_page in enumerate(pdf_doc, start=1)
            ]
            # 中文：本项目暂不做 OCR，因此无文本层的 PDF 要尽早失败。
            # English: OCR is out of scope, so PDFs without text layers fail early.
            if not any(page.paragraphs for page in pages):
                raise ValueError("PDF contains no extractable text. OCR is not supported.")
            sections = self.section_detector.detect(pdf_doc, pages)
        return Document(str(path), pages, sections)
