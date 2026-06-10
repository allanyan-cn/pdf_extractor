"""Testable services shared by the Streamlit Rule Studio application."""

from __future__ import annotations

import hashlib
import json
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import pdfplumber
import pymupdf

from pdf_extractor.extractor.table_extractor import TableExtractor
from pdf_extractor.indexer.fts_indexer import FTSIndexer
from pdf_extractor.models import BBox, Document, ExecutionReport, Paragraph
from pdf_extractor.parser.pdf_parser import PDFParser
from pdf_extractor.rules.rule_executor import RuleExecutor
from pdf_extractor.rules.rule_schema import ExtractionRule
from pdf_extractor.utils.show_table_structure import (
    NUMBER_PATTERN,
    _headers_from_page_words,
    _is_useful_candidate,
)
from pdf_extractor.utils.text import strip_footnote_markers


@dataclass(frozen=True)
class StudioTable:
    """A table candidate shown by Rule Studio."""

    table_index: int
    rows: list[list[Any]]
    bbox: BBox
    row_headers: list[str]
    column_headers: list[str]
    column_count: int

    @property
    def label(self) -> str:
        """Return the stable label and shape shown in the table selector."""
        return (
            f"Table {self.table_index} - "
            f"{len(self.rows)} rows × {self.column_count} columns"
        )

    @property
    def summary(self) -> str:
        """Return a compact structural summary."""
        return self.label


def new_rule_payload(index: int = 1) -> dict[str, Any]:
    """Return a minimal editable rule payload."""
    return {
        "id": f"rule_{index:03d}",
        "name": "New extraction rule",
        "scope": None,
        "keywords": [],
        "extract_type": "text",
        "target": "Target content",
        "priority": index - 1,
    }


def rule_to_payload(rule: ExtractionRule) -> dict[str, Any]:
    """Serialize a rule while omitting optional fields at their defaults."""
    payload: dict[str, Any] = {
        "id": rule.id,
        "name": rule.name,
        "scope": rule.scope,
        "keywords": list(rule.keywords),
        "extract_type": rule.extract_type,
        "target": rule.target,
        "priority": rule.priority,
    }
    optional_values = {
        "within_heading": rule.within_heading,
        "table_selector": rule.table_selector,
        "normalization": rule.normalization,
    }
    payload.update(
        {field: value for field, value in optional_values.items() if value is not None}
    )
    if rule.table_strategy != "auto":
        payload["table_strategy"] = rule.table_strategy
    if rule.llm_input != "page_image":
        payload["llm_input"] = rule.llm_input
    return payload


def validate_rule_payload(payload: dict[str, Any]) -> ExtractionRule:
    """Validate one editable rule payload with the core schema."""
    return ExtractionRule.from_dict(payload)


def parse_rules_json(data: str | bytes) -> list[ExtractionRule]:
    """Parse and validate the same JSON format accepted by RuleLoader."""
    payload = json.loads(data)
    if not isinstance(payload, dict) or set(payload) != {"rules"}:
        raise ValueError("Rule JSON must contain exactly one top-level 'rules' field.")
    if not isinstance(payload["rules"], list) or not payload["rules"]:
        raise ValueError("'rules' must be a non-empty list.")
    rules = [validate_rule_payload(item) for item in payload["rules"]]
    ids = [rule.id for rule in rules]
    if len(ids) != len(set(ids)):
        raise ValueError("Rule ids must be unique.")
    return sorted(rules, key=lambda rule: (rule.priority, rule.id))


def rules_json(rules: Iterable[ExtractionRule]) -> str:
    """Return formatted, UTF-8 friendly rule JSON."""
    payload = {"rules": [rule_to_payload(rule) for rule in rules]}
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def materialize_pdf(
    content: bytes,
    filename: str,
    cache_dir: str | Path | None = None,
) -> Path:
    """Persist uploaded PDF bytes at a deterministic temporary path."""
    if not content:
        raise ValueError("The uploaded PDF is empty.")
    digest = hashlib.sha256(content).hexdigest()[:16]
    safe_name = Path(filename).name or "document.pdf"
    destination_dir = Path(cache_dir or tempfile.gettempdir()) / "pdf_extractor_rule_studio"
    destination_dir.mkdir(parents=True, exist_ok=True)
    destination = destination_dir / f"{digest}-{safe_name}"
    if not destination.exists() or destination.read_bytes() != content:
        destination.write_bytes(content)
    return destination


def parse_uploaded_pdf(
    content: bytes,
    filename: str,
    cache_dir: str | Path | None = None,
) -> Document:
    """Persist and parse an uploaded text-based PDF."""
    return PDFParser().parse(str(materialize_pdf(content, filename, cache_dir)))


def execute_rules(
    document: Document,
    rules: list[ExtractionRule],
) -> ExecutionReport:
    """Execute validated rules against a parsed document."""
    with FTSIndexer() as indexer:
        indexer.build(document)
        return RuleExecutor(indexer).execute_with_diagnostics(document, rules)


def load_page_tables(file_path: str, page_number: int) -> list[StudioTable]:
    """Find every useful local table on one physical PDF page."""
    extractor = TableExtractor(include_adjacent_pages=False)
    with pdfplumber.open(file_path) as pdf:
        if page_number < 1 or page_number > len(pdf.pages):
            raise ValueError(f"Page number must be between 1 and {len(pdf.pages)}.")
        page = pdf.pages[page_number - 1]
        candidates = [
            candidate
            for candidate in extractor._page_candidates(page, page_number)
            if _is_useful_candidate(candidate)
        ]
        tables = []
        for index, candidate in enumerate(candidates, start=1):
            row_headers, column_headers = _headers_from_page_words(page, candidate)
            tables.append(
                StudioTable(
                    table_index=index,
                    rows=candidate.rows,
                    bbox=candidate.bbox,
                    row_headers=row_headers,
                    column_headers=column_headers,
                    column_count=_table_column_count(
                        page,
                        candidate.bbox,
                        candidate.rows,
                        row_headers,
                    ),
                )
            )
    return tables


def _table_column_count(
    page: Any,
    bbox: BBox,
    rows: list[list[Any]],
    row_headers: list[str],
) -> int:
    """Estimate visible columns when pdfplumber captures only one value column."""
    extracted_count = max((len(row) for row in rows), default=0)
    words = page.extract_words(use_text_flow=True, keep_blank_chars=False)
    numeric_x_positions = sorted(
        float(word["x1"])
        for word in words
        if bbox.y0 - 2 <= float(word["top"]) <= bbox.y1 + 2
        and float(word["x0"]) >= bbox.x0 - 12
        and NUMBER_PATTERN.fullmatch(
            re.sub(
                r"\s+",
                "",
                strip_footnote_markers(str(word["text"])),
            )
        )
    )
    numeric_columns: list[float] = []
    for x0 in numeric_x_positions:
        if not numeric_columns or abs(x0 - numeric_columns[-1]) > 12:
            numeric_columns.append(x0)
    coordinate_count = len(numeric_columns) + (1 if row_headers else 0)
    return max(extracted_count, coordinate_count)


def section_label(document: Document, section_id: str | None) -> str:
    """Return the full path for a section id."""
    section = next(
        (candidate for candidate in document.sections if candidate.id == section_id),
        None,
    )
    if section is None:
        return ""
    return " > ".join(section.path or [section.title])


def section_for_page(document: Document, page_number: int) -> Any | None:
    """Return the latest TOC section active at a physical page."""
    active = [
        (index, section)
        for index, section in enumerate(document.sections)
        if section.start_page <= page_number
    ]
    if not active:
        return None
    return max(
        active,
        key=lambda item: (
            item[1].start_page,
            item[0],
        ),
    )[1]


def scope_for_page(document: Document, page_number: int) -> str | None:
    """Return the full TOC path active at a physical page."""
    section = section_for_page(document, page_number)
    return section_label(document, section.id) if section is not None else None


def document_tree(document: Document) -> tuple[list[dict[str, Any]], list[str]]:
    """Return nested tree items and their preorder section ids."""
    if not document.sections:
        return [], []

    section_ids = {section.id for section in document.sections}
    children_by_parent: dict[str | None, list[Any]] = {}
    for section in document.sections:
        parent_id = (
            section.parent_id
            if section.parent_id in section_ids
            else None
        )
        children_by_parent.setdefault(parent_id, []).append(section)

    preorder_ids: list[str] = []

    def build_items(parent_id: str | None) -> list[dict[str, Any]]:
        items = []
        for section in children_by_parent.get(parent_id, []):
            preorder_ids.append(section.id)
            children = build_items(section.id)
            item: dict[str, Any] = {
                "label": section.title,
                "tag": _section_page_range(section.start_page, section.end_page),
                "tooltip": section_label(document, section.id),
            }
            if children:
                item["children"] = children
            items.append(item)
        return items

    return build_items(None), preorder_ids


def _section_page_range(start_page: int, end_page: int | None) -> str:
    """Format a compact page range for the document tree."""
    if end_page in (None, start_page):
        return f"p.{start_page}"
    return f"p.{start_page}-{end_page}"


def resolve_tree_selection(
    document: Document,
    section_ids: list[str],
    selection: int | list[int] | None,
) -> tuple[str, int] | None:
    """Map an Ant Design Tree selection to a section id and start page."""
    if isinstance(selection, list):
        selection = selection[-1] if selection else None
    if not isinstance(selection, int) or not 0 <= selection < len(section_ids):
        return None
    section_id = section_ids[selection]
    section = next(
        (candidate for candidate in document.sections if candidate.id == section_id),
        None,
    )
    if section is None:
        return None
    return section.id, section.start_page


def filter_paragraphs(
    document: Document,
    query: str = "",
    section_id: str | None = None,
    limit: int = 50,
) -> list[Paragraph]:
    """Filter paragraphs for interactive browsing in reading order."""
    normalized_query = query.strip().casefold()
    matches = [
        paragraph
        for paragraph in document.paragraphs
        if (section_id is None or paragraph.section_id == section_id)
        and (
            not normalized_query
            or normalized_query in paragraph.text.casefold()
        )
    ]
    return matches[: max(limit, 0)]


def render_page_png(
    file_path: str,
    page_number: int,
    highlights: Iterable[dict[str, float]] = (),
    scale: float = 1.5,
) -> bytes:
    """Render a PDF page and overlay PDF-coordinate highlight rectangles."""
    with pymupdf.open(file_path) as pdf:
        if page_number < 1 or page_number > len(pdf):
            raise ValueError(f"Page number must be between 1 and {len(pdf)}.")
        page = pdf[page_number - 1]
        for bbox in highlights:
            page.draw_rect(
                pymupdf.Rect(
                    bbox["x0"],
                    bbox["y0"],
                    bbox["x1"],
                    bbox["y1"],
                ),
                color=(0.95, 0.45, 0.1),
                fill=(1.0, 0.85, 0.25),
                fill_opacity=0.28,
                width=1.2,
                overlay=True,
            )
        pixmap = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
        return pixmap.tobytes("png")


def report_payload(document: Document, report: ExecutionReport) -> dict[str, Any]:
    """Build the standard extraction output payload."""
    return {
        "file_path": document.file_path,
        "results": [result.to_dict() for result in report.results],
        "diagnostics": [diagnostic.to_dict() for diagnostic in report.diagnostics],
    }


def result_table_rows(report: ExecutionReport) -> list[dict[str, Any]]:
    """Build user-facing result rows with normalized values first."""
    return [
        {
            "rule_id": result.rule_id,
            "normalized_value": (
                result.normalized_value
                if result.normalized_value is not None
                else result.value
            ),
            "raw_value": result.value,
            "page": result.page_number,
            "confidence": result.confidence,
            "source": result.source_text,
        }
        for result in report.results
    ]


def report_json(document: Document, report: ExecutionReport) -> str:
    """Serialize an execution report as formatted JSON."""
    return json.dumps(
        report_payload(document, report),
        ensure_ascii=False,
        indent=2,
    ) + "\n"
