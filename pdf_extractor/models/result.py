"""提取结果和诊断信息的数据模型。

Extraction result and diagnostic data models.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from pdf_extractor.utils.bbox import BBox


@dataclass
class ExtractionResult:
    """带来源坐标的结构化提取结果。

    A structured value with traceable source coordinates.
    """

    rule_id: str
    value: Any
    source_text: str | None
    page_number: int
    bbox: BBox
    confidence: float | None = None
    rule_name: str | None = None
    extract_type: str | None = None
    target: str | None = None
    normalized_value: Any | None = None
    paragraph_id: str | None = None
    section_title: str | None = None
    section_path: list[str] | None = None
    bbox_source: str = "paragraph"
    page_numbers: list[int] | None = None
    bboxes: list[BBox] | None = None

    def to_dict(self) -> dict[str, Any]:
        """返回可 JSON 序列化的字典。

        Return a JSON-serializable representation.
        """
        return asdict(self)


@dataclass(frozen=True)
class RuleDiagnostic:
    """用于排查提取问题的单条规则执行状态。

    A per-rule execution status for troubleshooting extraction results.
    """

    rule_id: str
    status: str
    message: str
    scope: str | None = None
    within_heading: str | None = None
    section_id: str | None = None
    candidate_count: int = 0
    result_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        """返回可 JSON 序列化的字典。

        Return a JSON-serializable representation.
        """
        return asdict(self)


@dataclass
class ExecutionReport:
    """一次规则执行的结果和诊断信息。

    Extraction results and diagnostics from a rule execution run.
    """

    results: list[ExtractionResult]
    diagnostics: list[RuleDiagnostic]
