"""文档模型和提取结果模型。

Document and extraction result data models.
"""

from pdf_extractor.models.document import Document, Page, Paragraph, Section, Word
from pdf_extractor.models.result import ExecutionReport, ExtractionResult, RuleDiagnostic
from pdf_extractor.utils.bbox import BBox

__all__ = [
    "BBox",
    "Document",
    "ExecutionReport",
    "ExtractionResult",
    "Page",
    "Paragraph",
    "Section",
    "RuleDiagnostic",
    "Word",
]
