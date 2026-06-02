"""Tests for PDF parsing and section detection."""

from pathlib import Path

import pymupdf
import pytest

from pdf_extractor.parser.pdf_parser import PDFParser


def _save_pdf(path: Path, *, toc: bool = False, empty: bool = False) -> Path:
    document = pymupdf.open()
    first = document.new_page()
    if not empty:
        first.insert_text((72, 72), "Chapter 1 Overview", fontsize=18)
        first.insert_text((72, 110), "Revenue increased to RMB 3,200 million.", fontsize=11)
        second = document.new_page()
        second.insert_text((72, 72), "Section 2 Results", fontsize=16)
        second.insert_text((72, 110), "Net income was 12.5 billion.", fontsize=11)
        if toc:
            document.set_toc([[1, "Chapter 1 Overview", 1], [2, "Section 2 Results", 2]])
    document.save(path)
    document.close()
    return path


def test_parser_extracts_pages_paragraphs_bbox_and_toc_sections(tmp_path: Path) -> None:
    parsed = PDFParser().parse(str(_save_pdf(tmp_path / "toc.pdf", toc=True)))

    assert len(parsed.pages) == 2
    assert parsed.pages[0].page_number == 1
    assert parsed.pages[0].paragraphs[0].bbox.x0 > 0
    assert parsed.pages[0].paragraphs[0].words[0].text == "Chapter"
    assert parsed.pages[0].paragraphs[0].words[0].bbox.x0 > 0
    assert [section.title for section in parsed.sections] == [
        "Chapter 1 Overview",
        "Section 2 Results",
    ]
    assert parsed.sections[0].parent_id is None
    assert parsed.sections[0].path == ["Chapter 1 Overview"]
    assert parsed.sections[1].parent_id == "s_0001"
    assert parsed.sections[1].path == ["Chapter 1 Overview", "Section 2 Results"]
    assert parsed.pages[1].paragraphs[-1].section_id == "s_0002"


def test_parser_detects_headings_without_toc(tmp_path: Path) -> None:
    parsed = PDFParser().parse(str(_save_pdf(tmp_path / "headings.pdf")))

    assert [section.title for section in parsed.sections] == [
        "Chapter 1 Overview",
        "Section 2 Results",
    ]
    assert parsed.sections[0].level == 1
    assert parsed.sections[1].level == 2
    assert parsed.sections[1].parent_id == "s_0001"
    assert parsed.sections[1].path == ["Chapter 1 Overview", "Section 2 Results"]
    assert parsed.pages[1].paragraphs[-1].section_id == "s_0002"


def test_parser_assigns_same_page_toc_sections_by_heading_position(tmp_path: Path) -> None:
    path = tmp_path / "same-page-toc.pdf"
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 72), "Chapter 1 Overview", fontsize=18)
    page.insert_text((72, 110), "Overview text.", fontsize=11)
    page.insert_text((72, 180), "Section 1.1 Results", fontsize=16)
    page.insert_text((72, 220), "Results text.", fontsize=11)
    document.set_toc([[1, "Chapter 1 Overview", 1], [2, "Section 1.1 Results", 1]])
    document.save(path)
    document.close()

    parsed = PDFParser().parse(str(path))

    assert parsed.pages[0].paragraphs[1].section_id == "s_0001"
    assert parsed.pages[0].paragraphs[-1].section_id == "s_0002"


def test_parser_rejects_pdf_without_extractable_text(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="OCR is not supported"):
        PDFParser().parse(str(_save_pdf(tmp_path / "empty.pdf", empty=True)))


def test_parser_rejects_missing_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="does not exist"):
        PDFParser().parse(str(tmp_path / "missing.pdf"))
