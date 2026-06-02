"""TOC-based and heuristic section detection."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from pdf_extractor.models import Page, Paragraph, Section

HEADING_PATTERN = re.compile(
    r"^(?:"
    r"\d+(?:\.\d+)*[.)]?\s+\S+"
    r"|第[一二三四五六七八九十百千零〇两\d]+[章节篇部]\s*\S*"
    r"|Chapter\s+\d+\b.*"
    r"|Section\s+\d+\b.*"
    r")$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class _HeadingCandidate:
    title: str
    page_number: int
    y0: float
    level: int


class SectionDetector:
    """Detect sections from the TOC or simple heading heuristics."""

    def detect(self, pdf_doc: Any, pages: list[Page]) -> list[Section]:
        """Return detected sections and assign paragraphs to them."""
        toc = pdf_doc.get_toc(simple=False)
        if toc:
            toc = [item for item in toc if str(item[1]).strip()]
            sections = self._from_toc(toc, len(pages))
            self._assign_from_toc(pages, sections, toc)
            return sections

        candidates = self._find_heading_candidates(pdf_doc, pages)
        sections = self._from_candidates(candidates, len(pages))
        self._assign_by_heading_position(pages, sections, candidates)
        return sections

    def _from_toc(self, toc: list[list[Any]], page_count: int) -> list[Section]:
        sections = [
            Section(
                id=f"s_{index:04d}",
                title=str(title).strip(),
                level=int(level),
                start_page=max(1, min(int(page_number), page_count)),
            )
            for index, (level, title, page_number, *_rest) in enumerate(toc, start=1)
            if str(title).strip()
        ]
        self._set_end_pages(sections, page_count)
        self._assign_hierarchy(sections)
        return sections

    def _find_heading_candidates(
        self, pdf_doc: Any, pages: list[Page]
    ) -> list[_HeadingCandidate]:
        candidates: list[_HeadingCandidate] = []
        for page, pdf_page in zip(pages, pdf_doc, strict=True):
            body_size, spans = self._page_spans(pdf_page)
            for paragraph in page.paragraphs:
                font_size = max(
                    (
                        size
                        for text, size, bbox in spans
                        if text and paragraph.text in text and bbox[1] >= paragraph.bbox.y0 - 1
                    ),
                    default=0.0,
                )
                is_pattern = bool(HEADING_PATTERN.match(paragraph.text))
                is_short = len(paragraph.text) <= 80
                is_near_top = paragraph.bbox.y0 <= page.height * 0.35
                is_large = font_size > body_size * 1.15 if body_size else False
                if is_short and (is_pattern or (is_near_top and is_large)):
                    candidates.append(
                        _HeadingCandidate(
                            paragraph.text,
                            page.page_number,
                            paragraph.bbox.y0,
                            self._infer_level(paragraph.text),
                        )
                    )
        return candidates

    @staticmethod
    def _page_spans(pdf_page: Any) -> tuple[float, list[tuple[str, float, tuple[float, ...]]]]:
        spans: list[tuple[str, float, tuple[float, ...]]] = []
        for block in pdf_page.get_text("dict").get("blocks", []):
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    text = " ".join(span.get("text", "").split())
                    if text:
                        spans.append((text, float(span.get("size", 0.0)), tuple(span["bbox"])))
        body_size = sorted(size for _text, size, _bbox in spans)[len(spans) // 2] if spans else 0.0
        return body_size, spans

    @staticmethod
    def _infer_level(title: str) -> int:
        numeric = re.match(r"^(\d+(?:\.\d+)*)", title)
        if numeric:
            return numeric.group(1).count(".") + 1
        if re.match(r"^第[一二三四五六七八九十百千零〇两\d]+节", title):
            return 2
        if re.match(r"^Section\s+", title, re.IGNORECASE):
            return 2
        return 1

    def _from_candidates(
        self, candidates: list[_HeadingCandidate], page_count: int
    ) -> list[Section]:
        sections = [
            Section(
                id=f"s_{index:04d}",
                title=candidate.title,
                level=candidate.level,
                start_page=candidate.page_number,
            )
            for index, candidate in enumerate(candidates, start=1)
        ]
        self._set_end_pages(sections, page_count)
        self._assign_hierarchy(sections)
        return sections

    @staticmethod
    def _set_end_pages(sections: list[Section], page_count: int) -> None:
        for index, section in enumerate(sections):
            next_start = sections[index + 1].start_page if index + 1 < len(sections) else page_count + 1
            section.end_page = max(section.start_page, next_start - 1)

    @staticmethod
    def _assign_hierarchy(sections: list[Section]) -> None:
        """Assign parent ids and title paths from section levels."""
        stack: list[Section] = []
        for section in sections:
            while stack and stack[-1].level >= section.level:
                stack.pop()
            parent = stack[-1] if stack else None
            section.parent_id = parent.id if parent else None
            section.path = [*(parent.path if parent else []), section.title]
            stack.append(section)

    @classmethod
    def _assign_from_toc(
        cls,
        pages: list[Page],
        sections: list[Section],
        toc: list[list[Any]],
    ) -> None:
        paragraphs_by_page = {page.page_number: page.paragraphs for page in pages}
        boundaries = [
            (
                section,
                section.start_page,
                cls._toc_boundary_y(
                    section.title,
                    paragraphs_by_page.get(section.start_page, []),
                    toc_item,
                ),
            )
            for section, toc_item in zip(sections, toc, strict=True)
        ]
        boundaries.sort(key=lambda boundary: (boundary[1], boundary[2]))
        active: Section | None = None
        boundary_index = 0
        for page in pages:
            for paragraph in page.paragraphs:
                paragraph_position = (page.page_number, paragraph.bbox.y0)
                while boundary_index < len(boundaries):
                    section, start_page, y0 = boundaries[boundary_index]
                    if (start_page, y0) > paragraph_position:
                        break
                    active = section
                    boundary_index += 1
                if active:
                    paragraph.section_id = active.id
                    active.paragraphs.append(paragraph.id)

    @staticmethod
    def _toc_boundary_y(
        title: str,
        paragraphs: list[Paragraph],
        toc_item: list[Any],
    ) -> float:
        normalized_title = " ".join(title.split()).casefold()
        for paragraph in paragraphs:
            if " ".join(paragraph.text.split()).casefold() == normalized_title:
                return paragraph.bbox.y0
        if len(toc_item) >= 4 and isinstance(toc_item[3], dict):
            destination = toc_item[3].get("to")
            if destination is not None and hasattr(destination, "y"):
                return float(destination.y)
        return 0.0

    @staticmethod
    def _assign_by_heading_position(
        pages: list[Page],
        sections: list[Section],
        candidates: list[_HeadingCandidate],
    ) -> None:
        boundaries = list(zip(sections, candidates, strict=True))
        active: Section | None = None
        for page in pages:
            for paragraph in page.paragraphs:
                for section, candidate in boundaries:
                    if (
                        candidate.page_number == page.page_number
                        and abs(candidate.y0 - paragraph.bbox.y0) < 1
                        and candidate.title == paragraph.text
                    ):
                        active = section
                        break
                if active:
                    paragraph.section_id = active.id
                    active.paragraphs.append(paragraph.id)
