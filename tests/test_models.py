"""Tests for document, result, and rule data models."""

import pytest

from pdf_extractor.models import BBox, Document, ExtractionResult, Page, Paragraph
from pdf_extractor.rules.rule_schema import ExtractionRule


def test_document_returns_paragraphs_in_page_order() -> None:
    first = Paragraph("p_000001", "first", 1, BBox(0, 0, 10, 10))
    second = Paragraph("p_000002", "second", 2, BBox(0, 0, 10, 10))
    document = Document("sample.pdf", [Page(1, 100, 100, [first]), Page(2, 100, 100, [second])])

    assert document.paragraphs == [first, second]


def test_bbox_rejects_inverted_coordinates() -> None:
    with pytest.raises(ValueError, match="non-negative rectangle"):
        BBox(10, 0, 5, 10)


def test_extraction_result_serializes_nested_bbox() -> None:
    result = ExtractionResult("rule_1", "12.5 亿元", "净收入为 12.5 亿元", 1, BBox(1, 2, 3, 4))

    assert result.to_dict()["bbox"] == {"x0": 1, "y0": 2, "x1": 3, "y1": 4}


def test_rule_from_dict_applies_default_priority() -> None:
    rule = ExtractionRule.from_dict(
        {
            "id": "net_income",
            "name": "提取净收入",
            "keywords": ["净收入"],
            "extract_type": "value",
            "target": "净收入金额",
        }
    )

    assert rule.priority == 0
    assert rule.scope is None


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"keywords": []}, "keywords"),
        ({"extract_type": "image"}, "extract_type"),
        ({"priority": True}, "priority"),
    ],
)
def test_rule_rejects_invalid_fields(override: dict[str, object], message: str) -> None:
    data = {
        "id": "net_income",
        "name": "提取净收入",
        "keywords": ["净收入"],
        "extract_type": "value",
        "target": "净收入金额",
        **override,
    }

    with pytest.raises(ValueError, match=message):
        ExtractionRule.from_dict(data)
