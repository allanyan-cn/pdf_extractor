"""Optional LLM fallback for ambiguous extraction candidates."""

from __future__ import annotations

import base64
import json
from typing import Any

import pymupdf

from pdf_extractor.models import BBox, Document, Paragraph
from pdf_extractor.rules.rule_schema import ExtractionRule


class LLMExtractor:
    """Optional extension point for ambiguous text extraction."""

    def __init__(self, client: Any) -> None:
        self.client = client

    def extract(self, rule: ExtractionRule, paragraphs: list[Paragraph]) -> list[Any]:
        """Reject calls until an explicit fallback contract is defined."""
        raise NotImplementedError("LLM fallback is optional and not implemented in V1.")


class MultimodalTableLLMExtractor:
    """Reconstruct a candidate table from small page images with the Responses API."""

    def __init__(self, client: Any, model: str = "gpt-4.1-mini") -> None:
        self.client = client
        self.model = model

    def extract_table(
        self,
        rule: ExtractionRule,
        document: Document,
        page_numbers: list[int],
        fallback_bbox: BBox,
    ) -> list[list[Any]] | None:
        """Return table rows from candidate PDF pages without trusting LLM coordinates."""
        content: list[dict[str, Any]] = [
            {
                "type": "input_text",
                "text": (
                    "Extract the table relevant to the requested target and keywords. "
                    "Return JSON only with a top-level 'rows' array. Preserve cell text. "
                    "Do not invent values. Use an empty string for visually merged blank cells.\n"
                    f"Target: {rule.target}\nKeywords: {', '.join(rule.keywords)}\n"
                    f"Candidate bbox for local traceability: {fallback_bbox.to_dict()}"
                ),
            }
        ]
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
        if not isinstance(rows, list) or any(not isinstance(row, list) for row in rows):
            raise ValueError("LLM table response must contain a list of row lists.")
        return rows or None
