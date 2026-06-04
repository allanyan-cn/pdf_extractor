"""确定性表格提取器，并支持可选多模态 LLM fallback。

Deterministic table extraction with optional multimodal LLM fallback.
"""

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
    """可选多模态表格重建助手的协议。

    Contract for an optional multimodal table reconstruction helper.
    """

    def extract_table(
        self,
        rule: ExtractionRule,
        document: Document,
        page_numbers: list[int],
        fallback_bbox: BBox,
    ) -> list[list[Any]] | None:
        """返回重建后的表格 rows；未识别到表格时返回 ``None``。

        Return reconstructed rows or ``None`` when no table is identified.
        """


@dataclass
class _TableCandidate:
    """本地或 LLM 识别出的表格候选。

    A table candidate detected locally or reconstructed by LLM.
    """

    rows: list[list[Any]]
    page_numbers: list[int]
    bboxes: list[BBox]
    page_heights: list[float]
    method: str

    @property
    def bbox(self) -> BBox:
        """返回当前结果模型使用的首页 bbox。

        Return the first-page bbox used by the current result schema.
        """
        return self.bboxes[0]


class TableExtractor:
    """提取有边框、无边框和跨页表格。

    Extract bordered, borderless, and cross-page tables.
    """

    def __init__(
        self,
        llm_assistant: TableLLMAssistant | None = None,
        *,
        include_adjacent_pages: bool = True,
    ) -> None:
        """初始化表格提取器。

        Initialize the table extractor.
        """
        self.llm_assistant = llm_assistant
        self.include_adjacent_pages = include_adjacent_pages

    def extract(
        self,
        rule: ExtractionRule,
        document: Document,
        paragraphs: list[Paragraph],
    ) -> list[ExtractionResult]:
        """按规则选择的 local/LLM 策略返回表格。

        Return tables using the rule-selected local/LLM strategy.
        """
        if rule.table_strategy == "llm":
            # 中文：llm 策略显式跳过本地表格解析，直接让 LLM 重建 rows。
            # English: The llm strategy explicitly skips local parsing and asks the LLM for rows.
            paragraphs_by_page = self._paragraphs_by_page(paragraphs)
            page_numbers = self._candidate_page_numbers(document, paragraphs_by_page)
            return self._extract_with_llm(rule, document, page_numbers, paragraphs_by_page)

        matched, page_numbers, paragraphs_by_page = self.extract_candidates(
            rule,
            document,
            paragraphs,
        )
        if matched:
            return [
                self._to_result(rule, candidate, paragraphs_by_page)
                for candidate in matched
            ]

        if rule.table_strategy == "local":
            # 中文：local 策略即使失败也不调用 LLM，适合成本/合规敏感场景。
            # English: The local strategy never calls LLM, useful for cost/compliance constraints.
            return []
        return self._extract_with_llm(rule, document, page_numbers, paragraphs_by_page)

    def extract_candidates(
        self,
        rule: ExtractionRule,
        document: Document,
        paragraphs: list[Paragraph],
        *,
        require_keyword_match: bool = True,
    ) -> tuple[list[_TableCandidate], list[int], dict[int, Paragraph]]:
        """返回本地表格候选，供整表或单元格提取复用。

        Return matched local table candidates for downstream cell extraction.
        """
        paragraphs_by_page = self._paragraphs_by_page(paragraphs)
        page_numbers = self._candidate_page_numbers(document, paragraphs_by_page)
        if not page_numbers:
            return [], [], paragraphs_by_page

        with pdfplumber.open(document.file_path) as pdf:
            # 中文：只扫描候选页和相邻页，避免把整份 PDF 都送入表格解析。
            # English: Scan only candidate/adjacent pages instead of the entire PDF.
            candidates = [
                candidate
                for page_number in page_numbers
                for candidate in self._page_candidates(pdf.pages[page_number - 1], page_number)
            ]
        merged = self._merge_cross_page_candidates(candidates)
        matched = (
            [
                candidate
                for candidate in merged
                if self._matches_keywords(candidate.rows, rule.keywords)
            ]
            if require_keyword_match
            else merged
        )
        return matched, page_numbers, paragraphs_by_page

    def _page_candidates(self, page: Any, page_number: int) -> list[_TableCandidate]:
        """提取单页表格候选，先有边框，失败后尝试文本布局。

        Extract table candidates from one page, bordered first then text-layout fallback.
        """
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
        """调用 pdfplumber 查找表格并转成内部候选对象。

        Find tables with pdfplumber and convert them to internal candidates.
        """
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
        """合并可能跨页延续的表格候选。

        Merge candidates that look like cross-page table continuations.
        """
        if not candidates:
            return []
        ordered = sorted(candidates, key=lambda candidate: (candidate.page_numbers[0], candidate.bbox.y0))
        merged: list[_TableCandidate] = []
        for candidate in ordered:
            if merged and self._can_merge(merged[-1], candidate):
                previous = merged[-1]
                rows = candidate.rows
                # 中文：续页重复表头时跳过当前页首行，避免表头重复出现在结果 rows 中。
                # English: Drop repeated continuation headers to avoid duplicate header rows.
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
        """判断两个相邻页表格是否应合并。

        Return whether two adjacent-page tables should be merged.
        """
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
        """计算两个表格 bbox 的横向重叠比例。

        Compute horizontal overlap ratio between two table bboxes.
        """
        overlap = max(0.0, min(first.x1, second.x1) - max(first.x0, second.x0))
        width = max(first.x1 - first.x0, second.x1 - second.x0)
        return overlap / width if width else 0.0

    @staticmethod
    def _same_header(first: list[Any], second: list[Any]) -> bool:
        """判断两个表头行清洗后是否相同。

        Return whether two header rows are equal after cell cleaning.
        """
        return [TableExtractor._clean_cell(cell) for cell in first] == [
            TableExtractor._clean_cell(cell) for cell in second
        ]

    @classmethod
    def _repair_merged_cells(cls, rows: list[list[Any]]) -> list[list[Any]]:
        """规整矩形 rows，并修复常见合并单元格空白。

        Normalize rectangular rows and repair common merged-header blanks.
        """
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
        # 中文：横向合并表头常表现为后一列表头为空，沿用左侧表头。
        # English: Horizontally merged headers often leave blank cells; copy from the left.
        for row in repaired[:1]:
            for index in range(1, width):
                if not row[index] and row[index - 1]:
                    row[index] = row[index - 1]
        # 中文：首列纵向合并常表现为后续行首列为空，沿用上一行行名。
        # English: Vertically merged first-column labels often leave blanks; copy from above.
        for index in range(1, len(repaired)):
            if not repaired[index][0] and repaired[index - 1][0]:
                repaired[index][0] = repaired[index - 1][0]
        return repaired

    @staticmethod
    def _clean_cell(cell: Any) -> Any:
        """清理单元格文本，统一 None 和多余空白。

        Clean cell text by normalizing None and extra whitespace.
        """
        return " ".join(cell.split()) if isinstance(cell, str) else "" if cell is None else cell

    @classmethod
    def _deduplicate_candidates(cls, candidates: list[_TableCandidate]) -> list[_TableCandidate]:
        """去除同页内容完全相同的重复表格候选。

        Remove duplicate candidates with identical text on the same page.
        """
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
        """把候选段落按页码索引。

        Index candidate paragraphs by page number.
        """
        return {paragraph.page_number: paragraph for paragraph in paragraphs}

    def _candidate_page_numbers(
        self, document: Document, paragraphs_by_page: dict[int, Paragraph]
    ) -> list[int]:
        """返回需要扫描的候选页码。

        Return page numbers to scan for tables.
        """
        page_numbers = set(paragraphs_by_page)
        if self.include_adjacent_pages:
            # 中文：表格可能从命中页延续到下一页，因此默认包含相邻下一页。
            # English: Tables may continue onto the next page, so include the following page by default.
            page_numbers.update(
                page_number + 1
                for page_number in paragraphs_by_page
                if page_number < len(document.pages)
            )
        return sorted(page_numbers)

    @staticmethod
    def _matches_keywords(rows: list[list[Any]], keywords: list[str]) -> bool:
        """判断表格文本是否匹配任一关键词。

        Return whether table text matches any configured keyword.
        """
        if not [keyword for keyword in keywords if keyword.strip()]:
            return True
        text = TableExtractor._table_text(rows).casefold()
        return any(keyword.casefold() in text for keyword in keywords)

    def _extract_with_llm(
        self,
        rule: ExtractionRule,
        document: Document,
        page_numbers: list[int],
        paragraphs_by_page: dict[int, Paragraph],
    ) -> list[ExtractionResult]:
        """调用可选 LLM 表格助手重建表格。

        Reconstruct a table using the optional LLM table assistant.
        """
        if not self.llm_assistant or not page_numbers or not paragraphs_by_page:
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
        # 中文：LLM 不负责坐标，bbox 回退到本地候选段落坐标并标记 table_llm。
        # English: LLM does not provide trusted coordinates; use local paragraph bbox as fallback.
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
        """把表格候选转换为统一的 ExtractionResult。

        Convert a table candidate into a common ExtractionResult.
        """
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
        """把 rows 转成用于匹配和 source_text 的纯文本。

        Convert rows to plain text for matching and source_text.
        """
        return "\n".join(
            " | ".join("" if cell is None else str(cell) for cell in row)
            for row in rows
        )

    @staticmethod
    def _bbox(raw_bbox: tuple[float, float, float, float]) -> BBox:
        """把 pdfplumber bbox tuple 转为 BBox。

        Convert a pdfplumber bbox tuple to BBox.
        """
        return BBox(*map(float, raw_bbox))
