"""可选 LLM fallback，用于传统方法难以稳定处理的候选。

Optional LLM fallback for ambiguous extraction candidates.
"""

from __future__ import annotations

import base64
import json
from typing import Any

import pymupdf

from pdf_extractor.models import BBox, Document, Paragraph
from pdf_extractor.rules.rule_schema import ExtractionRule


class LLMExtractor:
    """文本类 LLM fallback 的扩展点。

    Optional extension point for ambiguous text extraction.
    """

    def __init__(self, client: Any) -> None:
        """保存外部传入的 LLM client。

        Store the externally provided LLM client.
        """
        self.client = client

    def extract(self, rule: ExtractionRule, paragraphs: list[Paragraph]) -> list[Any]:
        """在明确文本 fallback 契约前拒绝调用。

        Reject calls until an explicit fallback contract is defined.
        """
        raise NotImplementedError("LLM fallback is optional and not implemented in V1.")


class MultimodalTableLLMExtractor:
    """使用 Responses API 从候选页面重建表格 rows。

    Reconstruct a candidate table from candidate pages with the Responses API.
    """

    def __init__(self, client: Any, model: str = "gpt-4.1-mini") -> None:
        """初始化多模态表格提取器。

        Initialize the multimodal table extractor.
        """
        self.client = client
        self.model = model

    def extract_table(
        self,
        rule: ExtractionRule,
        document: Document,
        page_numbers: list[int],
        fallback_bbox: BBox,
    ) -> list[list[Any]] | None:
        """从候选 PDF 页面返回表格 rows，但不信任 LLM 坐标。

        Return table rows from candidate PDF pages without trusting LLM coordinates.
        """
        # 中文：提示中明确要求只返回 rows，坐标仍由本地定位链路提供。
        # English: The prompt asks only for rows; coordinates remain owned by local locators.
        content: list[dict[str, Any]] = [
            {
                "type": "input_text",
                "text": (
                    "Extract the table relevant to the requested target and keywords. "
                    "Return JSON only with a top-level 'rows' array. Preserve cell text. "
                    "Do not invent values. Use an empty string for visually merged blank cells.\n"
                    f"Target: {rule.target}\nKeywords: {', '.join(rule.keywords)}\n"
                    f"Input mode: {rule.llm_input}\n"
                    f"Candidate bbox for local traceability: {fallback_bbox.to_dict()}"
                ),
            }
        ]
        if rule.llm_input == "text":
            # 中文：text 模式只发送候选页的解析段落，成本较低但依赖 PDF 文本顺序。
            # English: Text mode sends parsed paragraphs only; cheaper but dependent on text order.
            paragraphs = [
                paragraph
                for paragraph in document.paragraphs
                if paragraph.page_number in set(page_numbers)
            ]
            content.append(
                {
                    "type": "input_text",
                    "text": "\n".join(
                        f"[page {paragraph.page_number}] {paragraph.text}"
                        for paragraph in paragraphs
                    ),
                }
            )
        else:
            # 中文：page_image 模式发送候选页 PNG，适合复杂版式和无边框表格。
            # English: page_image mode sends page PNGs for complex layouts and borderless tables.
            with pymupdf.open(document.file_path) as pdf:
                for page_number in page_numbers:
                    page = pdf[page_number - 1]
                    pixmap = page.get_pixmap(matrix=pymupdf.Matrix(1.5, 1.5), alpha=False)
                    image = base64.b64encode(pixmap.tobytes("png")).decode("ascii")
                    content.append(
                        {
                            "type": "input_image",
                            "image_url": f"data:image/png;base64,{image}",
                        }
                    )

        response = self.client.responses.create(
            model=self.model,
            input=[{"role": "user", "content": content}],
            text={
                "format": {
                    "type": "json_schema",
                    "name": "table_rows",
                    "strict": True,
                    "schema": {
                        "type": "object",
                        "properties": {
                            "rows": {
                                "type": "array",
                                "items": {
                                    "type": "array",
                                    "items": {"type": ["string", "number", "null"]},
                                },
                            }
                        },
                        "required": ["rows"],
                        "additionalProperties": False,
                    },
                }
            },
        )
        payload = json.loads(response.output_text)
        rows = payload.get("rows")
        # 中文：严格校验 LLM 输出结构，避免自由文本进入下游表格处理。
        # English: Strictly validate LLM output so free-form text never enters table handling.
        if not isinstance(rows, list) or any(not isinstance(row, list) for row in rows):
            raise ValueError("LLM table response must contain a list of row lists.")
        return rows or None
