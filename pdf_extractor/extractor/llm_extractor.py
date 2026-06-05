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
    """使用 OpenAI-compatible API 从候选页面重建表格 rows。

    Reconstruct a candidate table from candidate pages with OpenAI-compatible APIs.
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
        # 中文：提示中明确要求只返回结构化内容，坐标仍由本地定位链路提供。
        # English: The prompt asks only for structured content; coordinates remain owned by local locators.
        content: list[dict[str, Any]] = [
            {
                "type": "input_text",
                "text": (
                    "Extract the complete target table relevant to the requested target and keywords, "
                    "not only the target data row. Return JSON only. Preserve cell text. "
                    "Do not invent values. Use an empty string for visually merged blank cells. "
                    "Prefer returning the table's column headers separately in 'column_headers'. "
                    "The column_headers array must align with row cells; include empty strings "
                    "for row-label columns or note columns when needed. "
                    "The 'rows' array should contain data rows from the same table. "
                    "If the header appears as a normal first row in the table, include it in "
                    "'column_headers' and do not duplicate it in 'rows'. "
                    "If a table selector is provided, 'column_headers' MUST contain the selected "
                    "column header when visible, and 'rows' MUST contain the selected row header. "
                    "Do not return only the target data row unless no header is visible.\n"
                    f"Target: {rule.target}\nKeywords: {', '.join(rule.keywords)}\n"
                    f"Table selector: {json.dumps(rule.table_selector, ensure_ascii=False)}\n"
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

        try:
            response = self.client.responses.create(
                model=self.model,
                input=[{"role": "user", "content": content}],
                text={
                    "format": {
                        "type": "json_schema",
                        "name": "table_rows",
                        "strict": True,
                        "schema": self._rows_schema(),
                    }
                },
            )
            output_text = response.output_text
            if not output_text or not output_text.strip():
                raise ValueError("Responses API returned empty output_text.")
            payload = json.loads(output_text)
        except Exception:
            # 中文：LM Studio 等 OpenAI-compatible 服务通常只实现 chat.completions。
            # English: LM Studio and similar OpenAI-compatible servers usually expose chat completions only.
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": self._to_chat_content(content)}],
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": "table_rows",
                        "schema": self._rows_schema(),
                    },
                },
            )
            output_text = response.choices[0].message.content
            payload = json.loads(output_text)
        rows = self._payload_to_rows(payload)
        # 中文：严格校验 LLM 输出结构，避免自由文本进入下游表格处理。
        # English: Strictly validate LLM output so free-form text never enters table handling.
        if not isinstance(rows, list) or any(not isinstance(row, list) for row in rows):
            raise ValueError("LLM table response must contain a list of row lists.")
        return rows or None

    @staticmethod
    def _rows_schema() -> dict[str, Any]:
        """返回表格 rows 的 JSON Schema。

        Return the JSON Schema for table rows.
        """
        return {
            "type": "object",
            "properties": {
                "column_headers": {
                    "type": "array",
                    "items": {"type": ["string", "number", "null"]},
                },
                "rows": {
                    "type": "array",
                    "items": {
                        "type": "array",
                        "items": {"type": ["string", "number", "null"]},
                    },
                }
            },
            "required": ["column_headers", "rows"],
            "additionalProperties": False,
        }

    @staticmethod
    def _payload_to_rows(payload: dict[str, Any]) -> list[list[Any]] | Any:
        """把 LLM payload 规整成下游使用的 rows。

        Normalize an LLM payload into downstream table rows.
        """
        rows = payload.get("rows")
        column_headers = payload.get("column_headers")
        if not isinstance(rows, list):
            return rows
        if not isinstance(column_headers, list) or not column_headers:
            return rows
        if rows and isinstance(rows[0], list) and rows[0] == column_headers:
            return rows
        width = max((len(row) for row in rows if isinstance(row, list)), default=0)
        if width and len(column_headers) < width:
            column_headers = [""] * (width - len(column_headers)) + column_headers
        elif width and len(column_headers) > width:
            column_headers = column_headers[-width:]
        return [column_headers, *rows]

    @staticmethod
    def _to_chat_content(content: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """把 Responses API content 转成 chat.completions content。

        Convert Responses API content into chat.completions content.
        """
        chat_content: list[dict[str, Any]] = []
        for item in content:
            if item["type"] == "input_text":
                chat_content.append({"type": "text", "text": item["text"]})
            elif item["type"] == "input_image":
                chat_content.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": item["image_url"]},
                    }
                )
        return chat_content
