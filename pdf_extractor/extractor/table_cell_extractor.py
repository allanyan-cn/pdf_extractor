"""从指定表格单元格中提取带类型的简单值。

Extract typed values from a selected table cell.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass
from typing import Any

import pdfplumber
import pymupdf

from pdf_extractor.extractor.table_extractor import TableExtractor, _TableCandidate
from pdf_extractor.extractor.text_extractor import TextExtractor
from pdf_extractor.extractor.value_extractor import ValueExtractor
from pdf_extractor.models import BBox, Document, ExtractionResult, Paragraph
from pdf_extractor.rules.rule_schema import ExtractionRule
from pdf_extractor.utils.text import normalize_match_text


@dataclass(frozen=True)
class TableCellExtractionReport:
    """表格单元格提取结果和精确失败原因。

    Selected table-cell extraction results plus a precise failure reason.
    """

    results: list[ExtractionResult]
    status: str
    message: str


class TableCellExtractor:
    """选择表格、行和列，然后提取简单类型值。

    Select a table, row, and column, then extract a simple typed value.
    """

    def __init__(
        self,
        table_extractor: TableExtractor | None = None,
        text_extractor: TextExtractor | None = None,
        value_extractor: ValueExtractor | None = None,
    ) -> None:
        """初始化表格单元格提取器及其依赖的 extractor。

        Initialize the table-cell extractor and its dependent extractors.
        """
        self.table_extractor = table_extractor or TableExtractor()
        self.text_extractor = text_extractor or TextExtractor()
        self.value_extractor = value_extractor or ValueExtractor()

    def extract(
        self,
        rule: ExtractionRule,
        document: Document,
        paragraphs: list[Paragraph],
    ) -> list[ExtractionResult]:
        """从选中的表格单元格返回一个带类型结果。

        Return one typed result from the selected table cell.
        """
        return self.extract_with_diagnostics(rule, document, paragraphs).results

    def extract_with_diagnostics(
        self,
        rule: ExtractionRule,
        document: Document,
        paragraphs: list[Paragraph],
    ) -> TableCellExtractionReport:
        """返回表格单元格提取结果和详细状态。

        Return typed cell extraction results and a detailed status.
        """
        if not rule.table_selector:
            return TableCellExtractionReport(
                [],
                "table_selector_not_configured",
                "The rule does not define table_selector.",
            )
        if rule.table_strategy == "llm":
            return self._extract_with_llm(rule, document, paragraphs)
        candidates, _page_numbers, paragraphs_by_page = self.table_extractor.extract_candidates(
            rule,
            document,
            paragraphs,
            require_keyword_match=False,
        )
        report = self._extract_from_candidates(
            rule,
            document,
            candidates,
            paragraphs_by_page,
            bbox_source="table_cell",
            confidence_cap=0.85,
        )
        if report.results or rule.table_strategy == "local":
            return report
        llm_report = self._extract_with_llm(rule, document, paragraphs)
        return llm_report if llm_report.results else report

    def _extract_from_candidates(
        self,
        rule: ExtractionRule,
        document: Document,
        candidates: list[_TableCandidate],
        paragraphs_by_page: dict[int, Paragraph],
        *,
        bbox_source: str,
        confidence_cap: float,
    ) -> TableCellExtractionReport:
        """从给定表格候选中抽取单元格。

        Extract a cell from the provided table candidates.
        """
        if not candidates:
            return TableCellExtractionReport(
                [],
                "table_not_found",
                "No table candidates were found near the matched paragraphs.",
            )
        table = self._select_table(rule.table_selector, candidates, document)
        if not table:
            return TableCellExtractionReport(
                [],
                "table_not_found",
                "No table matched table_title/table_index in table_selector.",
            )
        row_index = self._resolve_row_index(rule.table_selector, table, document)
        # 中文：先解析行，再解析列，让 diagnostics 能明确指出是行缺失还是列缺失。
        # English: Resolve row before column so diagnostics can identify the missing dimension.
        if row_index is None:
            return TableCellExtractionReport(
                [],
                "table_row_not_found",
                "No table row matched row_header/row_index in table_selector.",
            )
        if row_index >= len(table.rows):
            return TableCellExtractionReport(
                [],
                "table_row_not_found",
                "The configured row_index is outside the selected table.",
            )
        column_index = self._resolve_column_index(rule.table_selector, table, document)
        if column_index is None:
            return TableCellExtractionReport(
                [],
                "table_column_not_found",
                "No table column matched column_header/column_index in table_selector.",
            )
        if column_index >= len(table.rows[row_index]):
            return TableCellExtractionReport(
                [],
                "table_column_not_found",
                "The configured column_index is outside the selected row.",
            )
        value = self._value_with_adjacent_unit(
            rule,
            table,
            row_index,
            column_index,
        )
        if value == "":
            return TableCellExtractionReport(
                [],
                "table_cell_empty",
                "The selected table cell is empty.",
            )

        paragraph = paragraphs_by_page.get(table.page_numbers[0]) or next(
            iter(paragraphs_by_page.values()),
            None,
        )
        cell_bbox = self._cell_bbox(table, row_index, column_index)
        # 中文：把单元格包装成临时 Paragraph，复用 text/value extractor 的类型识别逻辑。
        # English: Wrap the cell as a temporary Paragraph to reuse text/value extractor logic.
        cell_paragraph = Paragraph(
            id=f"table_cell_{table.page_numbers[0]}_{row_index + 1}_{column_index + 1}",
            text=str(value),
            page_number=table.page_numbers[0],
            bbox=cell_bbox,
            section_id=paragraph.section_id if paragraph else None,
        )
        if rule.extract_type == "text":
            results = self.text_extractor.extract(rule, [cell_paragraph])
        else:
            results = self.value_extractor.extract(rule, [cell_paragraph])
        for result in results:
            # 中文：结果坐标必须指向单元格近似 bbox，而不是整段或整表 bbox。
            # English: Result coordinates must point to the approximate cell bbox, not paragraph/table bbox.
            result.paragraph_id = paragraph.id if paragraph else cell_paragraph.id
            result.bbox = cell_bbox
            result.bbox_source = bbox_source
            result.source_text = str(value)
            result.confidence = min(result.confidence or 0.8, confidence_cap)
        if not results:
            return TableCellExtractionReport(
                [],
                "table_cell_type_not_found",
                "The selected table cell did not contain the requested extract_type.",
            )
        return TableCellExtractionReport(
            results,
            "success",
            "Table cell extraction completed successfully.",
        )

    @staticmethod
    def _value_with_adjacent_unit(
        rule: ExtractionRule,
        table: _TableCandidate,
        row_index: int,
        column_index: int,
    ) -> str:
        """Attach a standalone preceding percentage unit to its numeric value."""
        value = TableExtractor._clean_cell(table.rows[row_index][column_index])
        if (
            rule.extract_type != "percentage"
            or "%" in value
            or row_index <= 0
            or column_index >= len(table.rows[row_index - 1])
        ):
            return value
        previous_value = TableExtractor._clean_cell(
            table.rows[row_index - 1][column_index]
        )
        return f"{value}%" if previous_value == "%" else value

    def _extract_with_llm(
        self,
        rule: ExtractionRule,
        document: Document,
        paragraphs: list[Paragraph],
    ) -> TableCellExtractionReport:
        """用 LLM 重建表格，再用 pdfplumber 解析清晰边框 PDF 后抽单元格。

        Rebuild the table with LLM, parse a clean bordered PDF with pdfplumber,
        then extract the selected cell.
        """
        if not self.table_extractor.llm_assistant:
            return TableCellExtractionReport(
                [],
                "table_llm_not_configured",
                "No LLM table assistant is configured.",
            )
        paragraphs_by_page = TableExtractor._paragraphs_by_page(paragraphs)
        page_numbers = self.table_extractor._candidate_page_numbers(
            document,
            paragraphs_by_page,
        )
        paragraph = paragraphs_by_page.get(min(paragraphs_by_page)) if paragraphs_by_page else None
        if not page_numbers or not paragraph:
            return TableCellExtractionReport(
                [],
                "table_not_found",
                "No pages were available for LLM table reconstruction.",
            )
        rows = self.table_extractor.llm_assistant.extract_table(
            rule,
            document,
            page_numbers,
            paragraph.bbox,
        )
        if not rows:
            return TableCellExtractionReport(
                [],
                "table_not_found",
                "LLM did not reconstruct a table for the selected location.",
            )
        source_paragraph = self._source_paragraph_for_llm_rows(
            rule,
            paragraphs,
            rows,
        ) or paragraph
        candidate = self._llm_rows_to_candidate(
            rows,
            document,
            [source_paragraph.page_number],
            source_paragraph.bbox,
        )
        report = self._extract_from_candidates(
            rule,
            document,
            [candidate],
            paragraphs_by_page,
            bbox_source="table_llm_cell",
            confidence_cap=0.7,
        )
        if report.results or report.status != "table_column_not_found":
            return report
        augmented_rows = self._augment_llm_rows_with_local_header(
            rule,
            document,
            page_numbers,
            rows,
        )
        if not augmented_rows:
            return report
        augmented_candidate = self._llm_rows_to_candidate(
            augmented_rows,
            document,
            [source_paragraph.page_number],
            source_paragraph.bbox,
        )
        return self._extract_from_candidates(
            rule,
            document,
            [augmented_candidate],
            paragraphs_by_page,
            bbox_source="table_llm_cell",
            confidence_cap=0.65,
        )

    @classmethod
    def _llm_rows_to_candidate(
        cls,
        rows: list[list[Any]],
        document: Document,
        page_numbers: list[int],
        fallback_bbox: BBox,
    ) -> _TableCandidate:
        """把 LLM rows 渲染成清晰网格 PDF，再用 pdfplumber 抽回候选表。

        Render LLM rows into a clean grid PDF and extract them back with pdfplumber.
        """
        repaired_rows = TableExtractor._repair_merged_cells(rows)
        rendered_rows = cls._extract_rows_from_rendered_grid(repaired_rows)
        return _TableCandidate(
            rendered_rows or repaired_rows,
            page_numbers,
            [fallback_bbox],
            [document.pages[page_number - 1].height for page_number in page_numbers],
            "table_llm_cell",
        )

    @classmethod
    def _augment_llm_rows_with_local_header(
        cls,
        rule: ExtractionRule,
        document: Document,
        page_numbers: list[int],
        rows: list[list[Any]],
    ) -> list[list[Any]] | None:
        """当 LLM 只返回数据行时，从本地段落补回列头行。

        When LLM returns only data rows, recover the column header row from
        local parsed paragraphs.
        """
        if not rule.table_selector or not rows:
            return None
        column_header = rule.table_selector.get("column_header")
        if not isinstance(column_header, str):
            return None
        target_width = max(len(row) for row in rows)
        normalized_column = cls._normalize(column_header)
        for paragraph in document.paragraphs:
            if paragraph.page_number not in set(page_numbers):
                continue
            if normalized_column not in cls._normalize(paragraph.text):
                continue
            header = cls._header_tokens_from_text(paragraph.text)
            if len(header) < 2:
                continue
            if not header or not any(normalized_column == cls._normalize(cell) for cell in header):
                continue
            if len(header) < target_width:
                header = [""] * (target_width - len(header)) + header
            elif len(header) > target_width:
                header = header[-target_width:]
            return [header, *rows]
        return None

    @classmethod
    def _source_paragraph_for_llm_rows(
        cls,
        rule: ExtractionRule,
        paragraphs: list[Paragraph],
        rows: list[list[Any]],
    ) -> Paragraph | None:
        """选择 LLM 单元格结果在原 PDF 中的最佳来源段落。

        Select the best original-PDF source paragraph for an LLM cell result.
        """
        if not rule.table_selector:
            return None
        row_header = rule.table_selector.get("row_header")
        if not isinstance(row_header, str):
            return None
        normalized_header = cls._normalize(row_header)
        if not any(
            normalized_header in cls._normalize("".join(str(cell) for cell in row))
            for row in rows
        ):
            return None
        row_values = [
            cls._normalize(str(cell))
            for row in rows
            if normalized_header in cls._normalize("".join(str(cell) for cell in row))
            for cell in row
            if cls._normalize(str(cell)) and cls._normalize(str(cell)) != normalized_header
        ]
        matches: list[tuple[int, Paragraph]] = []
        for paragraph in paragraphs:
            normalized_text = cls._normalize(paragraph.text)
            if normalized_header not in normalized_text:
                continue
            score = sum(1 for value in row_values if value in normalized_text)
            matches.append((score, paragraph))
        if not matches:
            return None
        return max(matches, key=lambda item: item[0])[1]

    @staticmethod
    def _header_tokens_from_text(text: str) -> list[str]:
        """从短表头文本中提取 note/年份等列头 token。

        Extract note/year-like column header tokens from a short header string.
        """
        return re.findall(r"(?i)\bnote\b|\b\d{4}\b", text)

    @classmethod
    def _extract_rows_from_rendered_grid(cls, rows: list[list[Any]]) -> list[list[Any]]:
        """用 pdfplumber 从重绘的清晰边框表格中抽取 rows。

        Extract rows from a redrawn clean bordered table with pdfplumber.
        """
        if not rows:
            return []
        pdf_bytes = cls._render_rows_to_bordered_pdf(rows)
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            tables = pdf.pages[0].find_tables()
            if not tables:
                return []
            extracted_rows = tables[0].extract()
        return TableExtractor._repair_merged_cells(extracted_rows)

    @staticmethod
    def _render_rows_to_bordered_pdf(rows: list[list[Any]]) -> bytes:
        """把 rows 渲染成有明显边框线的 PDF。

        Render rows as a PDF with clear cell borders.
        """
        column_count = max((len(row) for row in rows), default=1)
        row_count = max(len(rows), 1)
        margin = 36.0
        cell_width = 120.0
        cell_height = 30.0
        width = margin * 2 + column_count * cell_width
        height = margin * 2 + row_count * cell_height
        pdf = pymupdf.open()
        page = pdf.new_page(width=width, height=height)
        x0 = margin
        y0 = margin
        x1 = x0 + column_count * cell_width
        y1 = y0 + row_count * cell_height
        for column_index in range(column_count + 1):
            x = x0 + column_index * cell_width
            page.draw_line((x, y0), (x, y1), width=0.8, color=(0, 0, 0))
        for row_index in range(row_count + 1):
            y = y0 + row_index * cell_height
            page.draw_line((x0, y), (x1, y), width=0.8, color=(0, 0, 0))
        for row_index, row in enumerate(rows):
            for column_index in range(column_count):
                value = row[column_index] if column_index < len(row) else ""
                rect = pymupdf.Rect(
                    x0 + column_index * cell_width + 3,
                    y0 + row_index * cell_height + 3,
                    x0 + (column_index + 1) * cell_width - 3,
                    y0 + (row_index + 1) * cell_height - 3,
                )
                page.insert_textbox(rect, str(value), fontsize=8)
        pdf_bytes = pdf.tobytes()
        pdf.close()
        return pdf_bytes

    @classmethod
    def _select_table(
        cls,
        selector: dict[str, Any],
        candidates: list[_TableCandidate],
        document: Document,
    ) -> _TableCandidate | None:
        """根据 table_title/table_index 选择目标表格。

        Select the target table by table_title/table_index.
        """
        tables = candidates
        page_number = selector.get("page_number")
        if page_number is not None:
            tables = [
                candidate
                for candidate in tables
                if int(page_number) in candidate.page_numbers
            ]
        title = selector.get("table_title")
        if isinstance(title, str) and title.strip():
            tables = [
                candidate
                for candidate in tables
                if cls._candidate_matches_title(candidate, title, document)
            ]
        index = int(selector.get("table_index", 1)) - 1
        tables = cls._prefer_tables_with_direct_selector_matches(selector, tables)
        if index < 0 or index >= len(tables):
            return None
        return tables[index]

    @classmethod
    def _prefer_tables_with_direct_selector_matches(
        cls,
        selector: dict[str, Any],
        candidates: list[_TableCandidate],
    ) -> list[_TableCandidate]:
        """优先选择 rows 中直接包含目标行列且单元格非空的候选表。

        Prefer candidates whose extracted rows directly contain the requested row,
        column, and a non-empty selected cell.
        """
        if not selector.get("row_header") and not selector.get("column_header"):
            return candidates
        matched: list[_TableCandidate] = []
        for candidate in candidates:
            row_index = cls._resolve_row_index_from_rows(selector, candidate)
            column_index = cls._resolve_column_index_from_rows(selector, candidate)
            if row_index is None or column_index is None:
                continue
            if row_index >= len(candidate.rows) or column_index >= len(candidate.rows[row_index]):
                continue
            if TableExtractor._clean_cell(candidate.rows[row_index][column_index]) == "":
                continue
            matched.append(candidate)
        return matched or candidates

    @staticmethod
    def _candidate_matches_title(
        candidate: _TableCandidate,
        title: str,
        document: Document,
    ) -> bool:
        """判断表格是否匹配配置的标题。

        Return whether a table matches the configured title.
        """
        normalized_title = TableCellExtractor._normalize(title)
        if normalized_title in TableCellExtractor._normalize(
            TableExtractor._table_text(candidate.rows)
        ):
            return True
        for paragraph in document.paragraphs:
            if paragraph.page_number not in candidate.page_numbers:
                continue
            if normalized_title not in TableCellExtractor._normalize(paragraph.text):
                continue
            if paragraph.page_number != candidate.page_numbers[0]:
                return True
            # 中文：同页标题通常位于表格上方不远处，保留一个小的垂直容忍区间。
            # English: Same-page titles usually sit shortly above the table; keep a small tolerance.
            if paragraph.bbox.y1 <= candidate.bbox.y0 + 24:
                return True
        return False

    @classmethod
    def _resolve_row_index(
        cls,
        selector: dict[str, Any],
        table: _TableCandidate,
        document: Document,
    ) -> int | None:
        """解析目标行索引，优先使用显式 row_index。

        Resolve the target row index, preferring explicit row_index.
        """
        if "row_index" in selector:
            return int(selector["row_index"]) - 1
        row_header = selector.get("row_header")
        if not isinstance(row_header, str):
            return None
        row_index = cls._resolve_row_index_from_rows(selector, table)
        if row_index is not None:
            return row_index
        # 中文：当 pdfplumber rows 缺少行标题时，尝试从表格左侧页面文字恢复行名。
        # English: If pdfplumber rows miss labels, recover row names from left-side page words.
        return cls._resolve_row_index_from_page_words(row_header, table, document)

    @classmethod
    def _resolve_row_index_from_rows(
        cls,
        selector: dict[str, Any],
        table: _TableCandidate,
    ) -> int | None:
        """仅从 rows 文本解析行索引，不读取页面 words。

        Resolve a row index from rows only, without reading page words.
        """
        if "row_index" in selector:
            return int(selector["row_index"]) - 1
        row_header = selector.get("row_header")
        if not isinstance(row_header, str):
            return None
        normalized_header = cls._normalize(row_header)
        for index, row in enumerate(table.rows):
            if normalized_header in cls._normalize("".join(str(cell) for cell in row)):
                return index
            if any(
                normalized_header in cls._normalize(str(cell))
                for cell in row
            ):
                return index
        return None

    @classmethod
    def _resolve_column_index(
        cls,
        selector: dict[str, Any],
        table: _TableCandidate,
        document: Document,
    ) -> int | None:
        """解析目标列索引，优先使用显式 column_index。

        Resolve the target column index, preferring explicit column_index.
        """
        if "column_index" in selector:
            return int(selector["column_index"]) - 1
        column_header = selector.get("column_header")
        if not isinstance(column_header, str) or not table.rows:
            return None
        column_index = cls._resolve_column_index_from_rows(selector, table)
        if column_index is not None:
            return column_index
        # 中文：当表格 rows 缺少列标题时，尝试从表格上方或内部页面文字恢复列名。
        # English: If rows miss header cells, recover column labels from nearby page words.
        return cls._resolve_column_index_from_page_words(column_header, table, document)

    @classmethod
    def _resolve_column_index_from_rows(
        cls,
        selector: dict[str, Any],
        table: _TableCandidate,
    ) -> int | None:
        """仅从 rows 文本解析列索引，并避开标题日期中的年份。

        Resolve a column index from rows only, avoiding years in title/date rows.
        """
        if "column_index" in selector:
            return int(selector["column_index"]) - 1
        column_header = selector.get("column_header")
        if not isinstance(column_header, str) or not table.rows:
            return None
        normalized_header = cls._normalize(column_header)
        matches: list[tuple[int, int, int]] = []
        year_pattern = re.compile(r"^\d{4}$")
        for row in table.rows:
            row_text = cls._normalize("".join(str(cell) for cell in row))
            year_count = sum(
                1
                for cell in row
                if year_pattern.fullmatch(cls._normalize(str(cell)))
            )
            has_note = any(cls._normalize(str(cell)) == "note" for cell in row)
            looks_like_date_title = (
                "yearended" in row_text
                or "december" in row_text
                or "january" in row_text
            )
            for index, cell in enumerate(row):
                if normalized_header in cls._normalize(str(cell)):
                    score = 0
                    if year_count > 1:
                        score += 4
                    if has_note:
                        score += 2
                    if looks_like_date_title:
                        score -= 4
                    matches.append((score, len(matches), index))
        if not matches:
            return None
        return max(matches, key=lambda item: (item[0], -item[1]))[2]

    @classmethod
    def _resolve_row_index_from_page_words(
        cls,
        row_header: str,
        table: _TableCandidate,
        document: Document,
    ) -> int | None:
        """从页面文字中恢复行标题对应的表格行索引。

        Recover a row index by matching row labels from page words.
        """
        page_words = cls._page_words(document, table.page_numbers[0])
        label_lines = cls._word_lines(
            [
                word
                for word in page_words
                if float(word["x1"]) <= table.bbox.x0 + 12
            ]
        )
        value_lines = cls._word_lines(
            [
                word
                for word in page_words
                if cls._overlaps(float(word["x0"]), float(word["x1"]), table.bbox.x0, table.bbox.x1)
            ]
        )
        normalized_header = cls._normalize(row_header)
        label_lines = [
            line
            for line in label_lines
            if normalized_header in cls._normalize(line["text"])
            and table.bbox.y0 - 8 <= line["center_y"] <= table.bbox.y1 + 8
        ]
        if not label_lines:
            return None
        for label_line in sorted(label_lines, key=lambda line: line["center_y"]):
            # 中文：行名与数值通常在同一水平线上，取 y 坐标最接近的值行做映射。
            # English: Row labels and values usually share a baseline; map by nearest y line.
            nearest_values = sorted(
                value_lines,
                key=lambda line: abs(line["center_y"] - label_line["center_y"]),
            )
            for value_line in nearest_values[:3]:
                if abs(value_line["center_y"] - label_line["center_y"]) > 8:
                    continue
                row_index = cls._row_index_matching_value_line(table.rows, value_line["text"])
                if row_index is not None:
                    return row_index
        return None

    @classmethod
    def _resolve_column_index_from_page_words(
        cls,
        column_header: str,
        table: _TableCandidate,
        document: Document,
    ) -> int | None:
        """从页面文字中恢复列标题对应的表格列索引。

        Recover a column index by matching column labels from page words.
        """
        column_count = max(max((len(row) for row in table.rows), default=0), 0)
        if column_count <= 0:
            return None
        page_words = cls._page_words(document, table.page_numbers[0])
        normalized_header = cls._normalize(column_header)
        matches = [
            word
            for word in page_words
            if normalized_header in cls._normalize(str(word["text"]))
            and cls._overlaps(float(word["x0"]), float(word["x1"]), table.bbox.x0, table.bbox.x1)
            and table.bbox.y0 - 40 <= float(word["top"]) <= table.bbox.y0 + 40
        ]
        if not matches:
            return None
        match = sorted(matches, key=lambda word: (abs(float(word["top"]) - table.bbox.y0), float(word["x0"])))[0]
        center_x = (float(match["x0"]) + float(match["x1"])) / 2
        column_width = (table.bbox.x1 - table.bbox.x0) / column_count
        if column_width <= 0:
            return None
        column_index = int((center_x - table.bbox.x0) / column_width)
        return max(0, min(column_index, column_count - 1))

    @classmethod
    def _row_index_matching_value_line(
        cls,
        rows: list[list[Any]],
        value_line: str,
    ) -> int | None:
        """用值行文本反推 rows 中的行索引。

        Infer a row index in rows from a value line text.
        """
        normalized_line = cls._normalize(value_line)
        for index, row in enumerate(rows):
            for cell in row:
                normalized_cell = cls._normalize(str(cell))
                if normalized_cell and normalized_cell in normalized_line:
                    return index
        return None

    @staticmethod
    def _page_words(document: Document, page_number: int) -> list[dict[str, Any]]:
        """读取指定页面的 word 级文本和坐标。

        Read word-level text and coordinates from one page.
        """
        with pdfplumber.open(document.file_path) as pdf:
            page = pdf.pages[page_number - 1]
            if not hasattr(page, "extract_words"):
                return []
            return page.extract_words(
                x_tolerance=2,
                y_tolerance=3,
                keep_blank_chars=False,
            )

    @classmethod
    def _word_lines(cls, words: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """把 word 聚合成同一 y 坐标附近的文本行。

        Group words into text lines by nearby y coordinates.
        """
        lines: list[dict[str, Any]] = []
        for word in sorted(words, key=lambda item: (float(item["top"]), float(item["x0"]))):
            center_y = (float(word["top"]) + float(word["bottom"])) / 2
            target = next(
                (
                    line
                    for line in lines
                    if abs(line["center_y"] - center_y) <= 3
                ),
                None,
            )
            if target is None:
                # 中文：新建一行并持续维护其 bbox、中心线和拼接文本。
                # English: Create a line and keep its bbox, centerline, and joined text updated.
                target = {
                    "words": [],
                    "x0": float(word["x0"]),
                    "x1": float(word["x1"]),
                    "top": float(word["top"]),
                    "bottom": float(word["bottom"]),
                    "center_y": center_y,
                    "text": "",
                }
                lines.append(target)
            target["words"].append(word)
            target["x0"] = min(target["x0"], float(word["x0"]))
            target["x1"] = max(target["x1"], float(word["x1"]))
            target["top"] = min(target["top"], float(word["top"]))
            target["bottom"] = max(target["bottom"], float(word["bottom"]))
            target["center_y"] = (target["top"] + target["bottom"]) / 2
            target["words"].sort(key=lambda item: float(item["x0"]))
            target["text"] = " ".join(
                str(item["text"]) for item in target["words"]
            )
        return lines

    @staticmethod
    def _overlaps(first_x0: float, first_x1: float, second_x0: float, second_x1: float) -> bool:
        """判断两个横向区间是否重叠。

        Return whether two horizontal ranges overlap.
        """
        return min(first_x1, second_x1) > max(first_x0, second_x0)

    @staticmethod
    def _cell_bbox(table: _TableCandidate, row_index: int, column_index: int) -> BBox:
        """按表格 bbox 和行列网格估算单元格 bbox。

        Estimate a cell bbox from the table bbox and row/column grid.
        """
        row_count = max(len(table.rows), 1)
        column_count = max(max((len(row) for row in table.rows), default=1), 1)
        row_height = (table.bbox.y1 - table.bbox.y0) / row_count
        column_width = (table.bbox.x1 - table.bbox.x0) / column_count
        return BBox(
            table.bbox.x0 + column_index * column_width,
            table.bbox.y0 + row_index * row_height,
            table.bbox.x0 + (column_index + 1) * column_width,
            table.bbox.y0 + (row_index + 1) * row_height,
        )

    @staticmethod
    def _normalize(value: str) -> str:
        """移除分隔符并大小写折叠，用于标题/行列名匹配。

        Remove separators and case-fold for title/header matching.
        """
        return re.sub(r"[\W_]+", "", normalize_match_text(value))
