"""Extraction result data models."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from pdf_extractor.utils.bbox import BBox


@dataclass
class ExtractionResult:
    """A structured value with traceable source coordinates."""

    rule_id: str
    value: Any
    source_text: str | None
    page_number: int
    bbox: BBox
    confidence: float | None = None
    rule_name: str | None = None
    extract_type: str | None = None
    target: str | None = None
    paragraph_id: str | None = None
    section_title: str | None = None
    section_path: list[str] | None = None
    bbox_source: str = "paragraph"
    page_numbers: list[int] | None = None
    bboxes: list[BBox] | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation."""
        return asdict(self)


@dataclass(frozen=True)
class RuleDiagnostic:
    """A per-rule execution status for troubleshooting extraction results."""

    rule_id: str
    status: str
    message: str
    scope: str | None = None
    section_id: str | None = None
    candidate_count: int = 0
    result_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation."""
        return asdict(self)


@dataclass
class ExecutionReport:
    """Extraction results and diagnostics from a rule execution run."""

    results: list[ExtractionResult]
    diagnostics: list[RuleDiagnostic]
