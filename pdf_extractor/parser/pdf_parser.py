"""PDF parsing orchestration."""

from __future__ import annotations

from pathlib import Path

import pymupdf

from pdf_extractor.models import Document
from pdf_extractor.parser.paragraph_builder import ParagraphBuilder
from pdf_extractor.parser.section_detector import SectionDetector


class PDFParser:
    """Parse text-based PDF files into document models."""

    def __init__(
        self,
        paragraph_builder: ParagraphBuilder | None = None,
        section_detector: SectionDetector | None = None,
    ) -> None:
        self.paragraph_builder = paragraph_builder or ParagraphBuilder()
        self.section_detector = section_detector or SectionDetector()

    def parse(self, file_path: str) -> Document:
        """Parse a PDF and reject files without extractable text."""
        path = Path(file_path)
        if not path.is_file():
            raise FileNotFoundError(f"PDF file does not exist: {file_path}")
        with pymupdf.open(path) as pdf_doc:
            pages = [
                self.paragraph_builder.build_page(pdf_page, page_number)
                for page_number, pdf_page in enumerate(pdf_doc, start=1)
            ]
            if not any(page.paragraphs for page in pages):
                raise ValueError("PDF contains no extractable text. OCR is not supported.")
            sections = self.section_detector.detect(pdf_doc, pages)
        return Document(str(path), pages, sections)
