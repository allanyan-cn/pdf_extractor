"""Deterministic complex table extraction with optional multimodal LLM fallback."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

import pdfplumber

from pdf_extractor.models import BBox, Document, ExtractionResult, Paragraph
from pdf_extractor.rules.rule_schema import ExtractionRule

BORDERLESS_TABLE_SETTINGS = {
    "vertical_strategy": "text",
    "horizontal_strategy": "text",
    "intersection_tolerance": 5,
    "snap_tolerance": 4,
    "join_tolerance": 4,
    "min_words_vertical": 2,
    "min_words_horizontal": 1,
}


class TableLLMAssistant(Protocol):
    """Contract for an optional multimodal table reconstruction helper."""

    def extract_table(
        self,
        rule: ExtractionRule,
        document: Document,
        page_numbers: list[int],
        fallback_bbox: BBox,
    ) -> list[list[Any]] | None:
        """Return reconstructed rows or ``None`` when no table is identified."""


@dataclass
class _TableCandidate:
    rows: list[list[Any]]
    page_numbers: list[int]
    bboxes: list[BBox]
    page_heights: list[float]
    method: str

    @property
    def bbox(self) -> BBox:
        """Return the first-page bbox used by the current result schema."""
        return self.bboxes[0]


class TableExtractor:
    """Extract bordered, borderless, and cross-page tables."""

    def __init__(
        self,
        llm_assistant: TableLLMAssistant | None = None,
        *,
        include_adjacent_pages: bool = True,
    ) -> None:
        self.llm_assistant = llm_assistant
        self.include_adjacent_pages = include_adjacent_pages

    def extract(
        self,
        rule: ExtractionRule,
        document: Document,
        paragraphs: list[Paragraph],
    ) -> list[ExtractionResult]:
        """Return locally extracted tables, then try the optional LLM fallback."""
        paragraphs_by_page = self._paragraphs_by_page(paragraphs)
        page_numbers = self._candidate_page_numbers(document, paragraphs_by_page)
        if not page_numbers:
            return []

        with pdfplumber.open(document.file_path) as pdf:
            candidates = [
                candidate
                for page_number in page_numbers
                for candidate in self._page_candidates(pdf.pages[page_number - 1], page_number)
            ]
        merged = self._merge_cross_page_candidates(candidates)
        matched = [
            candidate for candidate in merged if self._matches_keywords(candidate.rows, rule.keywords)
        ]
        if matched:
            return [
                self._to_result(rule, candidate, paragraphs_by_page)
                for candidate in matched
            ]

        return self._extract_with_llm(rule, document, page_numbers, paragraphs_by_page)

    def _page_candidates(self, page: Any, page_number: int) -> list[_TableCandidate]:
        candidates = self._find_tables(page, page_number, None, "table")
        if not candidates:
            candidates = self._find_tables(
                page, page_number, BORDERLESS_TABLE_SETTINGS, "table_text"
            )
        return self._deduplicate_candidates(candidates)

    def _find_tables(
        self,
        page: Any,
        page_number: int,
        table_settings: dict[str, Any] | None,
        method: str,
    ) -> list[_TableCandidate]:
        tables = page.find_tables(table_settings=table_settings) if table_settings else page.find_tables()
        candidates: list[_TableCandidate] = []
        for table in tables:
            rows = table.extract()
            if rows:
                candidates.append(
                    _TableCandidate(
                        rows=self._repair_merged_cells(rows),
                        page_numbers=[page_number],
                        bboxes=[self._bbox(table.bbox)],
                        page_heights=[float(page.height)],
                        method=method,
                    )
                )
        return candidates

    def _merge_cross_page_candidates(
        self, candidates: list[_TableCandidate]
    ) -> list[_TableCandidate]:
        if not candidates:
            return []
        ordered = sorted(candidates, key=lambda candidate: (candidate.page_numbers[0], candidate.bbox.y0))
        merged: list[_TableCandidate] = []
        for candidate in ordered:
            if merged and self._can_merge(merged[-1], candidate):
                previous = merged[-1]
                rows = candidate.rows
                if self._same_header(previous.rows[0], rows[0]):
                    rows = rows[1:]
                previous.rows.extend(rows)
                previous.page_numbers.extend(candidate.page_numbers)
                previous.bboxes.extend(candidate.bboxes)
                previous.page_heights.extend(candidate.page_heights)
                previous.method = "table_cross_page"
            else:
                merged.append(candidate)
        return merged

    @staticmethod
    def _can_merge(previous: _TableCandidate, current: _TableCandidate) -> bool:
        return (
            current.page_numbers[0] == previous.page_numbers[-1] + 1
            and len(current.rows[0]) == len(previous.rows[0])
            and (
                TableExtractor._same_header(previous.rows[0], current.rows[0])
                or (
                    TableExtractor._column_overlap(previous.bbox, current.bbox) >= 0.8
                    and previous.bbox.y1 >= previous.page_heights[-1] * 0.65
                    and current.bbox.y0 <= current.page_heights[0] * 0.35
                )
            )
        )

    @staticmethod
    def _column_overlap(first: BBox, second: BBox) -> float:
        overlap = max(0.0, min(first.x1, second.x1) - max(first.x0, second.x0))
        width = max(first.x1 - first.x0, second.x1 - second.x0)
        return overlap / width if width else 0.0

    @staticmethod
    def _same_header(first: list[Any], second: list[Any]) -> bool:
        return [TableExtractor._clean_cell(cell) for cell in first] == [
            TableExtractor._clean_cell(cell) for cell in second
        ]

    @classmethod
    def _repair_merged_cells(cls, rows: list[list[Any]]) -> list[list[Any]]:
        """Normalize rectangular rows and repair common merged-header blanks."""
        if not rows:
            return []
        width = max(len(row) for row in rows)
        repaired = [
            [cls._clean_cell(cell) for cell in row] + [""] * (width - len(row))
            for row in rows
        ]
        repaired = [row for row in repaired if any(cell != "" for cell in row)]
        if not repaired:
            return []
        for row in repaired[:1]:
            for index in range(1, width):
                if not row[index] and row[index - 1]:
                    row[index] = row[index - 1]
        for index in range(1, len(repaired)):
            if not repaired[index][0] and repaired[index - 1][0]:
                repaired[index][0] = repaired[index - 1][0]
        return repaired

    @staticmethod
    def _clean_cell(cell: Any) -> Any:
        return " ".join(cell.split()) if isinstance(cell, str) else "" if cell is None else cell

    @classmethod
    def _deduplicate_candidates(cls, candidates: list[_TableCandidate]) -> list[_TableCandidate]:
        unique: list[_TableCandidate] = []
        seen: set[tuple[int, str]] = set()
        for candidate in candidates:
            key = (candidate.page_numbers[0], cls._table_text(candidate.rows))
            if key not in seen:
                seen.add(key)
                unique.append(candidate)
        return unique

    @staticmethod
    def _paragraphs_by_page(paragraphs: list[Paragraph]) -> dict[int, Paragraph]:
        return {paragraph.page_number: paragraph for paragraph in paragraphs}

    def _candidate_page_numbers(
        self, document: Document, paragraphs_by_page: dict[int, Paragraph]
    ) -> list[int]:
        page_numbers = set(paragraphs_by_page)
        if self.include_adjacent_pages:
            page_numbers.update(
                page_number + 1
                for page_number in paragraphs_by_page
                if page_number < len(document.pages)
            )
        return sorted(page_numbers)

    @staticmethod
    def _matches_keywords(rows: list[list[Any]], keywords: list[str]) -> bool:
        text = TableExtractor._table_text(rows).casefold()
        return any(keyword.casefold() in text for keyword in keywords)

    def _extract_with_llm(
        self,
        rule: ExtractionRule,
        document: Document,
        page_numbers: list[int],
        paragraphs_by_page: dict[int, Paragraph],
    ) -> list[ExtractionResult]:
        if not self.llm_assistant:
            return []
        paragraph = paragraphs_by_page[min(paragraphs_by_page)]
        rows = self.llm_assistant.extract_table(
            rule,
            document,
            page_numbers,
            paragraph.bbox,
        )
        if not rows:
            return []
        candidate = _TableCandidate(
            self._repair_merged_cells(rows),
            page_numbers,
            [paragraph.bbox],
            [document.pages[page_number - 1].height for page_number in page_numbers],
            "table_llm",
        )
        return [self._to_result(rule, candidate, paragraphs_by_page)]

    def _to_result(
        self,
        rule: ExtractionRule,
        candidate: _TableCandidate,
        paragraphs_by_page: dict[int, Paragraph],
    ) -> ExtractionResult:
        paragraph = paragraphs_by_page.get(candidate.page_numbers[0]) or next(
            iter(paragraphs_by_page.values())
        )
        return ExtractionResult(
            rule_id=rule.id,
            rule_name=rule.name,
            extract_type=rule.extract_type,
            target=rule.target,
            value=candidate.rows,
            source_text=self._table_text(candidate.rows),
            page_number=candidate.page_numbers[0],
            page_numbers=candidate.page_numbers,
            bbox=candidate.bbox,
            bboxes=candidate.bboxes,
            paragraph_id=paragraph.id,
            bbox_source=candidate.method,
            confidence=0.75 if candidate.method == "table_llm" else 0.9,
        )

    @staticmethod
    def _table_text(rows: list[list[Any]]) -> str:
        return "\n".join(
            " | ".join("" if cell is None else str(cell) for cell in row)
            for row in rows
        )

    @staticmethod
    def _bbox(raw_bbox: tuple[float, float, float, float]) -> BBox:
        return BBox(*map(float, raw_bbox))
