"""Parsed PDF document data models."""

from __future__ import annotations

from dataclasses import dataclass, field

from pdf_extractor.utils.bbox import BBox


@dataclass(frozen=True)
class Word:
    """A word token with its PDF coordinates."""

    text: str
    bbox: BBox


@dataclass
class Paragraph:
    """A text block extracted from a PDF page."""

    id: str
    text: str
    page_number: int
    bbox: BBox
    section_id: str | None = None
    words: list[Word] = field(default_factory=list)


@dataclass
class Page:
    """A one-based PDF page with paragraph blocks."""

    page_number: int
    width: float
    height: float
    paragraphs: list[Paragraph] = field(default_factory=list)


@dataclass
class Section:
    """A document section derived from the TOC or heading heuristics."""

    id: str
    title: str
    level: int
    start_page: int
    end_page: int | None = None
    paragraphs: list[str] = field(default_factory=list)
    parent_id: str | None = None
    path: list[str] = field(default_factory=list)


@dataclass
class Document:
    """The parsed representation of a text-based PDF file."""

    file_path: str
    pages: list[Page] = field(default_factory=list)
    sections: list[Section] = field(default_factory=list)

    @property
    def paragraphs(self) -> list[Paragraph]:
        """Return document paragraphs in reading order."""
        return [
            paragraph
            for page in self.pages
            for paragraph in page.paragraphs
        ]
