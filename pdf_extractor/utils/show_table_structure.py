"""Print row-header and column-header candidates for tables on a PDF page."""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

import pdfplumber
import pymupdf

from pdf_extractor.extractor.table_extractor import TableExtractor, _TableCandidate

YEAR_PATTERN = re.compile(r"^(?:19|20)\d{2}$")
NUMBER_PATTERN = re.compile(
    r"^\(?[-+]?(?:[$€£¥]\s*)?(?:\d[\d,]*(?:\.\d+)?|\.\d+)%?\)?$"
)


@dataclass(frozen=True)
class TableStructure:
    """Rule-oriented row and column header candidates for one logical table."""

    table_index: int
    row_headers: list[str]
    column_headers: list[str]


def _resolve_page_index(path: Path, page_number: int) -> int:
    """Resolve a printed PDF page label before falling back to physical order."""
    if page_number < 1:
        raise ValueError("Page number must be a positive integer.")

    requested_label = str(page_number)
    with pymupdf.open(path) as pdf:
        for index, page in enumerate(pdf):
            if page.get_label() == requested_label:
                return index
        if page_number <= pdf.page_count:
            return page_number - 1
        raise ValueError(
            f"Page number {page_number} is outside the PDF page range "
            f"1-{pdf.page_count} and does not match a PDF page label."
        )


def _is_useful_candidate(candidate: _TableCandidate) -> bool:
    """Discard whole-page artifacts that duplicate one large text blob."""
    if not candidate.rows:
        return False
    cells = [
        str(cell).strip()
        for row in candidate.rows
        for cell in row
        if str(cell).strip()
    ]
    if len(candidate.rows) == 1 and cells:
        return len(set(cells)) > 1 and max(map(len, cells)) < 500
    return True


def _group_words(words: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Group pdfplumber words into visual lines."""
    lines: list[list[dict[str, Any]]] = []
    for word in sorted(words, key=lambda item: (float(item["top"]), float(item["x0"]))):
        for line in lines:
            if abs(float(line[0]["top"]) - float(word["top"])) <= 2.5:
                line.append(word)
                break
        else:
            lines.append([word])
    return [sorted(line, key=lambda item: float(item["x0"])) for line in lines]


def _unique(values: Sequence[str]) -> list[str]:
    """Return non-empty strings in first-seen order."""
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        cleaned = " ".join(value.split())
        key = cleaned.casefold()
        if cleaned and key not in seen:
            seen.add(key)
            result.append(cleaned)
    return result


def _fallback_headers(candidate: _TableCandidate) -> tuple[list[str], list[str]]:
    """Infer conventional first-row/first-column headers from extracted rows."""
    column_headers = (
        [str(cell) for cell in candidate.rows[0] if str(cell).strip()]
        if candidate.rows
        else []
    )
    row_headers = [
        str(row[0])
        for row in candidate.rows[1:]
        if row and str(row[0]).strip()
    ]
    return _unique(row_headers), _unique(column_headers)


def _headers_from_page_words(
    page: Any,
    candidate: _TableCandidate,
) -> tuple[list[str], list[str]]:
    """Recover table headers around a numeric table using page word coordinates."""
    lines = _group_words(page.extract_words(use_text_flow=True, keep_blank_chars=False))
    value_x0 = candidate.bbox.x0 - 12
    candidate_lines = [
        line
        for line in lines
        if candidate.bbox.y0 - 12
        <= float(line[0]["top"])
        <= candidate.bbox.y1 + 2
    ]

    column_words = [
        word
        for line in candidate_lines
        for word in line
        if (
            YEAR_PATTERN.fullmatch(str(word["text"]))
            and float(word["x0"]) >= value_x0
        )
        or (
            str(word["text"]).casefold() == "note"
            and float(word["x0"]) >= candidate.bbox.x0 - 70
        )
    ]
    column_words.sort(key=lambda word: float(word["x0"]))
    column_headers = _unique([str(word["text"]) for word in column_words])

    note_words = [
        word for word in column_words if str(word["text"]).casefold() == "note"
    ]
    label_x1 = (
        float(note_words[0]["x0"]) - 4
        if note_words
        else candidate.bbox.x0 - 12
    )
    row_headers: list[str] = []
    pending: list[str] = []
    data_started = False
    for line in candidate_lines:
        value_words = [
            word
            for word in line
            if float(word["x0"]) >= value_x0
            and NUMBER_PATTERN.fullmatch(str(word["text"]).replace(" ", ""))
        ]
        label = " ".join(
            str(word["text"])
            for word in line
            if float(word["x0"]) < label_x1
        ).strip()
        if value_words:
            data_started = True
            if label:
                full_label = " ".join([*pending, label])
                row_headers.append(full_label)
            pending = []
            continue
        if not data_started or not label:
            continue
        if label.startswith("(") and label.endswith(")"):
            pending = []
            continue
        if label.endswith(":"):
            row_headers.append(" ".join([*pending, label]))
            pending = []
        else:
            pending.append(label)

    fallback_rows, fallback_columns = _fallback_headers(candidate)
    descriptive_columns = [
        header
        for header in fallback_columns
        if not YEAR_PATTERN.fullmatch(header)
        and header.casefold() != "note"
    ]
    return (
        _unique(row_headers or fallback_rows),
        _unique(
            [*descriptive_columns, *column_headers]
            if column_headers
            else fallback_columns
        ),
    )


def load_table_structures(
    file_path: str | Path,
    page_number: int,
) -> list[TableStructure]:
    """Return rule-oriented headers for tables on a printed/labelled PDF page."""
    path = Path(file_path)
    if not path.is_file():
        raise FileNotFoundError(f"PDF file does not exist: {path}")

    page_index = _resolve_page_index(path, page_number)
    extractor = TableExtractor(include_adjacent_pages=False)
    with pdfplumber.open(path) as pdf:
        page = pdf.pages[page_index]
        candidates = [
            candidate
            for candidate in extractor._merge_cross_page_candidates(
                extractor._page_candidates(page, page_index + 1)
            )
            if _is_useful_candidate(candidate)
        ]
        structures = []
        for index, candidate in enumerate(candidates, start=1):
            row_headers, column_headers = _headers_from_page_words(page, candidate)
            structures.append(
                TableStructure(index, row_headers, column_headers)
            )
    return structures


def format_table_structures(
    page_number: int,
    tables: Sequence[TableStructure],
) -> str:
    """Format only headers that can be copied into table_selector rules."""
    if not tables:
        return f"Page {page_number}: no tables found."

    lines = [f"Page {page_number}: {len(tables)} table(s) found."]
    for table in tables:
        lines.extend(["", f"Table {table.table_index}", "  column_headers:"])
        lines.extend(
            f"    - {header}" for header in table.column_headers
        )
        if not table.column_headers:
            lines.append("    (none)")
        lines.append("  row_headers:")
        lines.extend(f"    - {header}" for header in table.row_headers)
        if not table.row_headers:
            lines.append("    (none)")
    return "\n".join(lines)


def build_argument_parser() -> argparse.ArgumentParser:
    """Create the command-line argument parser."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", help="Path to the PDF file.")
    parser.add_argument(
        "--page",
        required=True,
        type=int,
        help="Printed page number/PDF page label, with physical-page fallback.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Print detected row and column headers for one PDF page."""
    arguments = build_argument_parser().parse_args(argv)
    try:
        tables = load_table_structures(arguments.pdf, arguments.page)
    except (FileNotFoundError, OSError, RuntimeError, ValueError) as error:
        print(error, file=sys.stderr)
        return 1

    print(format_table_structures(arguments.page, tables))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
