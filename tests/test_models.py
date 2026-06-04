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
    result = ExtractionResult(
        "rule_1",
        "12.5 亿元",
        "净收入为 12.5 亿元",
        1,
        BBox(1, 2, 3, 4),
        normalized_value="12.5",
    )

    assert result.to_dict()["bbox"] == {"x0": 1, "y0": 2, "x1": 3, "y1": 4}
    assert result.to_dict()["normalized_value"] == "12.5"


def test_rule_from_dict_applies_default_priority() -> None:
    rule = ExtractionRule.from_dict(
        {
            "id": "net_income",
            "name": "提取净收入",
            "within_heading": "利润表",
            "keywords": ["净收入"],
            "extract_type": "value",
            "target": "净收入金额",
        }
    )

    assert rule.priority == 0
    assert rule.scope is None
    assert rule.within_heading == "利润表"


def test_rule_from_dict_defaults_missing_keywords_to_empty_list() -> None:
    rule = ExtractionRule.from_dict(
        {
            "id": "net_income",
            "name": "提取净收入",
            "extract_type": "value",
            "target": "净收入金额",
        }
    )

    assert rule.keywords == []


def test_rule_accepts_table_selector_for_simple_extract_type() -> None:
    rule = ExtractionRule.from_dict(
        {
            "id": "cell",
            "name": "cell",
            "keywords": ["利润表"],
            "extract_type": "percentage",
            "target": "同比增长率",
            "table_selector": {
                "table_title": "利润表",
                "row_header": "净收入",
                "column_header": "同比",
            },
        }
    )

    assert rule.table_selector == {
        "table_title": "利润表",
        "row_header": "净收入",
        "column_header": "同比",
    }


def test_rule_accepts_table_strategy_for_table_extract_type() -> None:
    rule = ExtractionRule.from_dict(
        {
            "id": "income_table",
            "name": "income table",
            "keywords": ["利润表"],
            "extract_type": "table",
            "target": "利润表",
            "table_strategy": "llm",
            "llm_input": "text",
        }
    )

    assert rule.table_strategy == "llm"
    assert rule.llm_input == "text"


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"table_strategy": "remote"}, "table_strategy"),
        ({"llm_input": "cropped_image"}, "llm_input"),
        ({"table_strategy": "local", "llm_input": "text"}, "llm_input"),
    ],
)
def test_rule_rejects_invalid_table_strategy_fields(
    override: dict[str, object],
    message: str,
) -> None:
    data = {
        "id": "income_table",
        "name": "income table",
        "keywords": ["利润表"],
        "extract_type": "table",
        "target": "利润表",
        **override,
    }

    with pytest.raises(ValueError, match=message):
        ExtractionRule.from_dict(data)


def test_rule_rejects_table_strategy_for_non_table_extract_type() -> None:
    data = {
        "id": "net_income",
        "name": "net income",
        "keywords": ["净收入"],
        "extract_type": "value",
        "target": "净收入",
        "table_strategy": "llm",
    }

    with pytest.raises(ValueError, match="extract_type 'table'"):
        ExtractionRule.from_dict(data)


def test_rule_accepts_normalization_options() -> None:
    rule = ExtractionRule.from_dict(
        {
            "id": "net_income",
            "name": "提取净收入",
            "keywords": ["净收入"],
            "extract_type": "number",
            "target": "净收入金额",
            "normalization": {"parentheses": "positive"},
        }
    )

    assert rule.normalization == {"parentheses": "positive"}


@pytest.mark.parametrize(
    ("normalization", "message"),
    [
        ({"parentheses": "unknown"}, "parentheses"),
        ({"unknown": True}, "Unexpected"),
        ("negative", "normalization"),
    ],
)
def test_rule_rejects_invalid_normalization(
    normalization: object,
    message: str,
) -> None:
    data = {
        "id": "net_income",
        "name": "提取净收入",
        "keywords": ["净收入"],
        "extract_type": "number",
        "target": "净收入金额",
        "normalization": normalization,
    }

    with pytest.raises(ValueError, match=message):
        ExtractionRule.from_dict(data)


@pytest.mark.parametrize("extract_type", ["text", "value", "table"])
def test_rule_rejects_normalization_for_non_normalizable_extract_types(
    extract_type: str,
) -> None:
    data = {
        "id": f"{extract_type}_rule",
        "name": extract_type,
        "keywords": ["净收入"],
        "extract_type": extract_type,
        "target": "净收入金额",
        "normalization": {"parentheses": "positive"},
    }

    with pytest.raises(ValueError, match="normalization can only be used"):
        ExtractionRule.from_dict(data)


@pytest.mark.parametrize(
    ("selector", "message"),
    [
        ({"row_header": "净收入"}, "column"),
        ({"column_header": "金额"}, "row"),
        ({"row_index": 0, "column_index": 1}, "row_index"),
        ({"row_index": 1, "column_index": True}, "column_index"),
        ({"row_index": 1, "column_index": 1, "unknown": 1}, "Unexpected"),
    ],
)
def test_rule_rejects_invalid_table_selector(
    selector: dict[str, object],
    message: str,
) -> None:
    data = {
        "id": "cell",
        "name": "cell",
        "keywords": ["利润表"],
        "extract_type": "value",
        "target": "金额",
        "table_selector": selector,
    }

    with pytest.raises(ValueError, match=message):
        ExtractionRule.from_dict(data)


def test_rule_rejects_table_selector_for_table_extract_type() -> None:
    data = {
        "id": "cell",
        "name": "cell",
        "keywords": ["利润表"],
        "extract_type": "table",
        "target": "利润表",
        "table_selector": {"row_index": 1, "column_index": 1},
    }

    with pytest.raises(ValueError, match="table_selector"):
        ExtractionRule.from_dict(data)


@pytest.mark.parametrize(
    "extract_type",
    ["text", "value", "percentage", "number", "date", "time", "table"],
)
def test_rule_accepts_supported_extract_types(extract_type: str) -> None:
    rule = ExtractionRule.from_dict(
        {
            "id": f"{extract_type}_rule",
            "name": extract_type,
            "keywords": ["净收入"],
            "extract_type": extract_type,
            "target": "净收入",
        }
    )

    assert rule.extract_type == extract_type


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"keywords": [""]}, "keywords"),
        ({"keywords": "净收入"}, "keywords"),
        ({"extract_type": "image"}, "extract_type"),
        ({"priority": True}, "priority"),
        ({"within_heading": 123}, "within_heading"),
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
