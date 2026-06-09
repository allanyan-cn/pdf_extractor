"""Tests for rule loading, extractors, and execution."""

import json
import logging
import os
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pymupdf
import pytest

from pdf_extractor.extractor.llm_extractor import LLMExtractor
from pdf_extractor.extractor.table_extractor import TableExtractor
from pdf_extractor.extractor.text_extractor import TextExtractor
from pdf_extractor.extractor.value_extractor import ValueExtractor
from pdf_extractor.indexer.fts_indexer import FTSIndexer
from pdf_extractor.models import BBox, Document, Page, Paragraph, Section, Word
from pdf_extractor.rules.rule_executor import RuleExecutor
from pdf_extractor.rules.rule_loader import RuleLoader
from pdf_extractor.rules.rule_schema import ExtractionRule
from pdf_extractor.utils.llm_connection import create_openai_client, load_dotenv_if_present
from pdf_extractor.utils.logging import configure_logging


def _rule(
    extract_type: str = "value",
    *,
    scope: str | None = None,
    within_heading: str | None = None,
) -> ExtractionRule:
    return ExtractionRule(
        id=f"{extract_type}_rule",
        name=f"提取 {extract_type}",
        scope=scope,
        keywords=["净收入"],
        extract_type=extract_type,
        target="净收入金额",
        within_heading=within_heading,
    )


def _document() -> Document:
    paragraph = Paragraph(
        "p_1",
        "公司净收入为 12.5 亿元，同比增长 8.6%。",
        1,
        BBox(10, 20, 200, 40),
        "s_2",
    )
    return Document(
        "sample.pdf",
        [Page(1, 300, 400, [paragraph])],
        [
            Section("s_1", "第一章", 1, 1, 1),
            Section("s_2", "第三节 财务表现", 2, 1, 1, ["p_1"]),
        ],
    )


def test_rule_loader_loads_and_sorts_rules_by_priority(tmp_path: Path) -> None:
    path = tmp_path / "rules.json"
    path.write_text(
        json.dumps(
            {
                "rules": [
                    {
                        "id": "low",
                        "name": "low",
                        "keywords": ["利润"],
                        "extract_type": "text",
                        "target": "利润",
                    },
                    {
                        "id": "high",
                        "name": "high",
                        "keywords": ["净收入"],
                        "extract_type": "value",
                        "target": "净收入",
                        "priority": 2,
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    assert [rule.id for rule in RuleLoader().load(str(path))] == ["high", "low"]


def test_rule_loader_rejects_duplicate_ids(tmp_path: Path) -> None:
    path = tmp_path / "rules.json"
    rule = {
        "id": "duplicate",
        "name": "duplicate",
        "keywords": ["净收入"],
        "extract_type": "text",
        "target": "净收入",
    }
    path.write_text(json.dumps({"rules": [rule, rule]}), encoding="utf-8")

    with pytest.raises(ValueError, match="unique"):
        RuleLoader().load(str(path))


def test_rule_loader_rejects_missing_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="does not exist"):
        RuleLoader().load(str(tmp_path / "missing.json"))


@pytest.mark.parametrize("payload", [{}, {"rules": []}, {"rules": {}, "extra": True}])
def test_rule_loader_rejects_invalid_top_level_payload(
    tmp_path: Path, payload: object
) -> None:
    path = tmp_path / "rules.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError):
        RuleLoader().load(str(path))


def test_text_extractor_returns_source_coordinates() -> None:
    paragraph = _document().paragraphs[0]

    result = TextExtractor().extract(_rule("text"), [paragraph])[0]

    assert result.value == paragraph.text
    assert result.bbox == paragraph.bbox
    assert result.paragraph_id == "p_1"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("净收入为 12.5 亿元", "12.5 亿元"),
        ("营业利润达到 RMB 3,200 million", "RMB 3,200 million"),
        ("同比增长 8.6%", "8.6%"),
        ("员工人数为 1200", "1200"),
    ],
)
def test_value_extractor_supports_simple_numeric_formats(text: str, expected: str) -> None:
    paragraph = Paragraph("p_1", text, 1, BBox(0, 0, 10, 10))

    result = ValueExtractor().extract(_rule(), [paragraph])[0]

    assert result.value == expected
    assert result.source_text == text
    assert result.bbox_source == "paragraph"


def test_value_extractor_deduplicates_same_value_on_one_page() -> None:
    paragraphs = [
        Paragraph("p_1", "净收入为 12.5 亿元", 1, BBox(0, 0, 10, 10)),
        Paragraph("p_2", "表格中的净收入为 12.5 亿元", 1, BBox(0, 20, 10, 30)),
    ]

    results = ValueExtractor().extract(_rule(), paragraphs)

    assert [result.value for result in results] == ["12.5 亿元"]


def test_value_extractor_prefers_amount_for_amount_target() -> None:
    paragraph = Paragraph(
        "p_1",
        "净收入为 12.5 亿元，同比增长 8.6%。",
        1,
        BBox(0, 0, 100, 20),
    )

    result = ValueExtractor().extract(_rule(), [paragraph])[0]

    assert result.value == "12.5 亿元"


def test_value_extractor_prefers_percentage_for_rate_target() -> None:
    paragraph = Paragraph(
        "p_1",
        "净收入为 12.5 亿元，同比增长 8.6%。",
        1,
        BBox(0, 0, 100, 20),
    )
    rule = ExtractionRule(
        "growth_rate",
        "提取同比增长率",
        None,
        ["同比增长"],
        "value",
        "同比增长率",
    )

    result = ValueExtractor().extract(rule, [paragraph])[0]

    assert result.value == "8.6%"


def test_value_extractor_percentage_type_returns_only_percentage() -> None:
    paragraph = Paragraph(
        "p_1",
        "净收入为 12.5 亿元，同比增长 8.6%。",
        1,
        BBox(0, 0, 100, 20),
    )
    rule = ExtractionRule(
        "growth_rate",
        "growth rate",
        None,
        ["同比增长"],
        "percentage",
        "同比增长",
    )

    result = ValueExtractor().extract(rule, [paragraph])[0]

    assert result.value == "8.6%"


def test_value_extractor_number_type_excludes_percentages_and_amounts() -> None:
    paragraph = Paragraph(
        "p_1",
        "净收入为 12.5 亿元，同比增长 8.6%，客户数量为 120。",
        1,
        BBox(0, 0, 100, 20),
    )
    rule = ExtractionRule(
        "customer_count",
        "customer count",
        None,
        ["客户数量"],
        "number",
        "客户数量",
    )

    result = ValueExtractor().extract(rule, [paragraph])[0]

    assert result.value == "120"


def test_value_extractor_number_type_returns_empty_for_percentage_only() -> None:
    paragraph = Paragraph("p_1", "同比增长 8.6%。", 1, BBox(0, 0, 100, 20))
    rule = ExtractionRule("growth", "growth", None, ["同比增长"], "number", "同比增长")

    assert ValueExtractor().extract(rule, [paragraph]) == []


@pytest.mark.parametrize(
    ("text", "keyword", "expected"),
    [
        ("报告日期为 2025-12-31。", "报告日期", "2025-12-31"),
        ("董事会批准日期为 31 Dec 2025。", "批准日期", "31 Dec 2025"),
        ("会议日期为 2025年12月31日。", "会议日期", "2025年12月31日"),
    ],
)
def test_value_extractor_date_type_returns_only_dates(
    text: str,
    keyword: str,
    expected: str,
) -> None:
    paragraph = Paragraph("p_1", text, 1, BBox(0, 0, 100, 20))
    rule = ExtractionRule("date_rule", "date rule", None, [keyword], "date", keyword)

    result = ValueExtractor().extract(rule, [paragraph])[0]

    assert result.value == expected


@pytest.mark.parametrize(
    ("text", "keyword", "expected"),
    [
        ("会议时间为 14:30。", "会议时间", "14:30"),
        ("Call starts at 9:05 AM.", "starts", "9:05 AM"),
        ("发布时间为 下午3时30分。", "发布时间", "下午3时30分"),
    ],
)
def test_value_extractor_time_type_returns_only_times(
    text: str,
    keyword: str,
    expected: str,
) -> None:
    paragraph = Paragraph("p_1", text, 1, BBox(0, 0, 100, 20))
    rule = ExtractionRule("time_rule", "time rule", None, [keyword], "time", keyword)

    result = ValueExtractor().extract(rule, [paragraph])[0]

    assert result.value == expected


def test_value_extractor_value_and_number_types_ignore_dates_and_times() -> None:
    paragraph = Paragraph(
        "p_1",
        "报告日期为 2025-12-31，会议时间为 14:30，客户数量为 120。",
        1,
        BBox(0, 0, 100, 20),
    )
    value_rule = ExtractionRule("value_rule", "value", None, ["客户数量"], "value", "客户数量")
    number_rule = ExtractionRule("number_rule", "number", None, ["客户数量"], "number", "客户数量")

    value_result = ValueExtractor().extract(value_rule, [paragraph])[0]
    number_result = ValueExtractor().extract(number_rule, [paragraph])[0]

    assert value_result.value == "120"
    assert number_result.value == "120"


def test_value_extractor_returns_span_bbox_from_word_coordinates() -> None:
    paragraph = Paragraph(
        "p_1",
        "净收入为 RMB 3,200 million，同比增长 8.6%。",
        1,
        BBox(0, 0, 200, 20),
        words=[
            Word("净收入为", BBox(0, 0, 30, 10)),
            Word("RMB", BBox(40, 0, 60, 10)),
            Word("3,200", BBox(65, 0, 95, 10)),
            Word("million，同比增长", BBox(100, 0, 170, 10)),
            Word("8.6%。", BBox(175, 0, 200, 10)),
        ],
    )

    result = ValueExtractor().extract(_rule(), [paragraph])[0]

    assert result.value == "RMB 3,200 million"
    assert result.bbox == BBox(40, 0, 170, 10)
    assert result.bbox_source == "span"
    assert result.confidence == 0.9


@pytest.mark.parametrize(
    ("text", "target", "keywords", "expected"),
    [
        ("净利润为 $3,200。", "净利润金额", ["净利润"], "$3,200"),
        ("净利润为 3,200 USD。", "净利润金额", ["净利润"], "3,200 USD"),
        ("净利润为 (1,234 万元)。", "净利润金额", ["净利润"], "(1,234 万元)"),
        (
            "Net income was (RMB 1,234 million).",
            "Net income amount",
            ["Net income"],
            "(RMB 1,234 million)",
        ),
        ("营业收入为 12.5亿。", "营业收入", ["营业收入"], "12.5亿"),
        ("同比下降 (8.6%)。", "同比增长率", ["同比"], "(8.6%)"),
        ("同比增长 8.6％。", "同比增长率", ["同比增长"], "8.6％"),
        ("资本充足率提高 25 bps。", "资本充足率", ["资本充足率"], "25 bps"),
        ("员工人数为 120 人。", "员工人数", ["员工人数"], "120 人"),
        ("股份数量为 120 万股。", "股份数量", ["股份数量"], "120 万股"),
        ("测量值为 2.5e6。", "测量值", ["测量值"], "2.5e6"),
        ("误差值为 -1.2e-3。", "误差值", ["误差值"], "-1.2e-3"),
    ],
)
def test_value_extractor_supports_extended_numeric_formats(
    text: str,
    target: str,
    keywords: list[str],
    expected: str,
) -> None:
    paragraph = Paragraph("p_1", text, 1, BBox(0, 0, 10, 10))
    rule = ExtractionRule("rule", "rule", None, keywords, "value", target)

    result = ValueExtractor().extract(rule, [paragraph])[0]

    assert result.value == expected


def test_value_extractor_ignores_dates_and_standalone_years() -> None:
    paragraph = Paragraph(
        "p_1",
        "截至 2024-03-31，2023 年净收入为 12.5 亿元。",
        1,
        BBox(0, 0, 100, 20),
    )

    result = ValueExtractor().extract(_rule(), [paragraph])[0]

    assert result.value == "12.5 亿元"


def test_value_extractor_skips_year_currency_header_and_note_number() -> None:
    paragraph = Paragraph(
        "p_1",
        "2024 $million Net interest income 3 5,955 6,366",
        1,
        BBox(0, 0, 100, 20),
    )
    rule = ExtractionRule(
        "net_interest",
        "net interest",
        None,
        ["Net interest income"],
        "value",
        "Net interest income",
    )

    result = ValueExtractor().extract(rule, [paragraph])[0]

    assert result.value == "5,955"
    assert result.normalized_value is None


@pytest.mark.parametrize(
    ("text", "extract_type", "target", "keywords", "expected_value", "expected_normalized"),
    [
        ("净利润为 $3,200。", "value", "净利润金额", ["净利润"], "$3,200", None),
        ("同比增长 8.6%。", "percentage", "同比增长率", ["同比增长"], "8.6%", "8.6"),
        ("客户数量为 120。", "number", "客户数量", ["客户数量"], "120", "120"),
        ("测量值为 2.5e6。", "number", "测量值", ["测量值"], "2.5e6", "2500000"),
        (
            "报告日期为 2025年12月31日。",
            "date",
            "报告日期",
            ["报告日期"],
            "2025年12月31日",
            "2025年12月31日",
        ),
        (
            "会议时间为 下午3时30分。",
            "time",
            "会议时间",
            ["会议时间"],
            "下午3时30分",
            "下午3时30分",
        ),
    ],
)
def test_value_extractor_normalizes_only_explicit_extract_types(
    text: str,
    extract_type: str,
    target: str,
    keywords: list[str],
    expected_value: str,
    expected_normalized: str | None,
) -> None:
    paragraph = Paragraph("p_1", text, 1, BBox(0, 0, 10, 10))
    rule = ExtractionRule("rule", "rule", None, keywords, extract_type, target)

    result = ValueExtractor().extract(rule, [paragraph])[0]

    assert result.value == expected_value
    assert result.normalized_value == expected_normalized


@pytest.mark.parametrize(
    ("parentheses_mode", "expected_normalized"),
    [
        ("negative", "-1234"),
        ("positive", "1234"),
        ("preserve", "(1,234)"),
    ],
)
def test_value_extractor_honors_parentheses_normalization_mode(
    parentheses_mode: str,
    expected_normalized: str,
) -> None:
    paragraph = Paragraph("p_1", "净收入为 (1,234)。", 1, BBox(0, 0, 10, 10))
    rule = ExtractionRule(
        "rule",
        "rule",
        None,
        ["净收入"],
        "number",
        "净收入",
        normalization={"parentheses": parentheses_mode},
    )

    result = ValueExtractor().extract(rule, [paragraph])[0]

    assert result.value == "(1,234)"
    assert result.normalized_value == expected_normalized


def test_value_extractor_returns_no_result_for_date_only_paragraph() -> None:
    paragraph = Paragraph("p_1", "报告日期为 2024-03-31。", 1, BBox(0, 0, 10, 10))
    rule = ExtractionRule("date", "date", None, ["报告日期"], "value", "报告日期")

    assert ValueExtractor().extract(rule, [paragraph]) == []


def test_value_extractor_prefers_amount_nearest_keyword() -> None:
    paragraph = Paragraph(
        "p_1",
        "总资产为 100 亿元，净收入为 20 亿元。",
        1,
        BBox(0, 0, 100, 20),
    )

    result = ValueExtractor().extract(_rule(), [paragraph])[0]

    assert result.value == "20 亿元"


def test_table_extractor_returns_pdfplumber_table_bbox(monkeypatch: pytest.MonkeyPatch) -> None:
    table = SimpleNamespace(
        bbox=(1, 2, 30, 40),
        extract=lambda: [["项目", "金额"], ["净收入", "12.5 亿元"]],
    )
    page = SimpleNamespace(height=100, find_tables=lambda **_kwargs: [table])
    opened_pdf = SimpleNamespace(pages=[page])
    monkeypatch.setattr(
        "pdf_extractor.extractor.table_extractor.pdfplumber.open",
        lambda _path: nullcontext(opened_pdf),
    )

    result = TableExtractor().extract(_rule("table"), _document(), _document().paragraphs)[0]

    assert result.value == [["项目", "金额"], ["净收入", "12.5 亿元"]]
    assert result.bbox == BBox(1, 2, 30, 40)
    assert result.bbox_source == "table"


def test_table_extractor_reads_real_single_page_table(tmp_path: Path) -> None:
    pdf_path = tmp_path / "table.pdf"
    pdf = pymupdf.open()
    page = pdf.new_page()
    for x in (50, 150, 250):
        page.draw_line((x, 50), (x, 110))
    for y in (50, 80, 110):
        page.draw_line((50, y), (250, y))
    page.insert_text((60, 70), "Item", fontsize=10)
    page.insert_text((160, 70), "Amount", fontsize=10)
    page.insert_text((60, 100), "Net income", fontsize=10)
    page.insert_text((160, 100), "12.5 billion", fontsize=10)
    pdf.save(pdf_path)
    pdf.close()
    paragraph = Paragraph("p_1", "Net income", 1, BBox(50, 50, 250, 110))
    document = Document(str(pdf_path), [Page(1, 300, 400, [paragraph])])
    rule = ExtractionRule(
        "table_rule", "Extract income table", None, ["Net income"], "table", "Income table"
    )

    result = TableExtractor().extract(rule, document, [paragraph])[0]

    assert result.value[1] == ["Net income", "12.5 billion"]
    assert result.bbox_source == "table"


def test_rule_executor_extracts_typed_cell_by_table_title_row_and_column(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    table = SimpleNamespace(
        bbox=(50, 100, 350, 190),
        extract=lambda: [["Item", "2025", "YoY"], ["Net income", "5,955", "8.6%"]],
    )
    page = SimpleNamespace(height=400, find_tables=lambda **_kwargs: [table])
    monkeypatch.setattr(
        "pdf_extractor.extractor.table_extractor.pdfplumber.open",
        lambda _path: nullcontext(SimpleNamespace(pages=[page])),
    )
    paragraph = Paragraph("p_1", "Consolidated income statement", 1, BBox(50, 50, 350, 80))
    document = Document("sample.pdf", [Page(1, 400, 400, [paragraph])])
    rule = ExtractionRule(
        "net_income_growth",
        "Extract net income growth",
        None,
        ["Consolidated income statement"],
        "percentage",
        "Net income growth",
        table_selector={
            "table_title": "Consolidated income statement",
            "row_header": "Net income",
            "column_header": "YoY",
        },
    )
    with FTSIndexer() as indexer:
        indexer.build(document)

        report = RuleExecutor(indexer).execute_with_diagnostics(document, [rule])

    assert report.diagnostics[0].status == "success"
    assert report.results[0].value == "8.6%"
    assert report.results[0].bbox_source == "table_cell"
    assert report.results[0].bbox == BBox(250, 145, 350, 190)


def test_rule_executor_extracts_table_cell_when_keywords_are_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    table = SimpleNamespace(
        bbox=(50, 100, 350, 190),
        extract=lambda: [["Item", "2025"], ["Net income", "5,955"]],
    )
    page = SimpleNamespace(height=400, find_tables=lambda **_kwargs: [table])
    monkeypatch.setattr(
        "pdf_extractor.extractor.table_extractor.pdfplumber.open",
        lambda _path: nullcontext(SimpleNamespace(pages=[page])),
    )
    paragraph = Paragraph("p_1", "Consolidated income statement", 1, BBox(50, 50, 350, 80))
    document = Document("sample.pdf", [Page(1, 400, 400, [paragraph])])
    rule = ExtractionRule(
        "net_income",
        "Extract net income",
        None,
        [],
        "number",
        "Net income",
        within_heading="Consolidated income statement",
        table_selector={"row_header": "Net income", "column_header": "2025"},
    )
    with FTSIndexer() as indexer:
        indexer.build(document)

        report = RuleExecutor(indexer).execute_with_diagnostics(document, [rule])

    assert report.diagnostics[0].status == "success"
    assert report.results[0].value == "5,955"
    assert report.results[0].normalized_value == "5955"


def test_rule_executor_resolves_table_cell_headers_from_page_words(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    table = SimpleNamespace(
        bbox=(200, 100, 400, 160),
        extract=lambda: [["", ""], ["5,955", "6,366"]],
    )
    words = [
        {"text": "2025", "x0": 230, "x1": 250, "top": 80, "bottom": 88},
        {"text": "2024", "x0": 330, "x1": 350, "top": 80, "bottom": 88},
        {"text": "Net", "x0": 50, "x1": 70, "top": 130, "bottom": 138},
        {"text": "interest", "x0": 72, "x1": 110, "top": 130, "bottom": 138},
        {"text": "income", "x0": 112, "x1": 150, "top": 130, "bottom": 138},
        {"text": "5,955", "x0": 230, "x1": 260, "top": 130, "bottom": 138},
        {"text": "6,366", "x0": 330, "x1": 360, "top": 130, "bottom": 138},
    ]
    page = SimpleNamespace(
        height=400,
        find_tables=lambda **_kwargs: [table],
        extract_words=lambda **_kwargs: words,
    )
    monkeypatch.setattr(
        "pdf_extractor.extractor.table_extractor.pdfplumber.open",
        lambda _path: nullcontext(SimpleNamespace(pages=[page])),
    )
    monkeypatch.setattr(
        "pdf_extractor.extractor.table_cell_extractor.pdfplumber.open",
        lambda _path: nullcontext(SimpleNamespace(pages=[page])),
    )
    paragraph = Paragraph("p_1", "Income statement", 1, BBox(50, 50, 350, 80))
    document = Document("sample.pdf", [Page(1, 400, 400, [paragraph])])
    rule = ExtractionRule(
        "net_interest_income",
        "Extract net interest income",
        None,
        [],
        "number",
        "Net interest income",
        table_selector={"row_header": "Net interest income", "column_header": "2025"},
    )
    with FTSIndexer() as indexer:
        indexer.build(document)

        report = RuleExecutor(indexer).execute_with_diagnostics(document, [rule])

    assert report.diagnostics[0].status == "success"
    assert report.results[0].value == "5,955"


def test_rule_executor_extracts_typed_cell_by_table_row_and_column_index(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_table = SimpleNamespace(
        bbox=(50, 100, 250, 160),
        extract=lambda: [["A", "B"], ["ignore", "0"]],
    )
    second_table = SimpleNamespace(
        bbox=(50, 200, 250, 260),
        extract=lambda: [["Meeting", "14:30"], ["Other", "15:45"]],
    )
    page = SimpleNamespace(
        height=400,
        find_tables=lambda **_kwargs: [first_table, second_table],
    )
    monkeypatch.setattr(
        "pdf_extractor.extractor.table_extractor.pdfplumber.open",
        lambda _path: nullcontext(SimpleNamespace(pages=[page])),
    )
    paragraph = Paragraph("p_1", "Schedule table", 1, BBox(50, 50, 350, 80))
    document = Document("sample.pdf", [Page(1, 400, 400, [paragraph])])
    rule = ExtractionRule(
        "meeting_time",
        "Extract meeting time",
        None,
        ["Schedule table"],
        "time",
        "Meeting time",
        table_selector={"table_index": 2, "row_index": 1, "column_index": 2},
    )
    with FTSIndexer() as indexer:
        indexer.build(document)

        report = RuleExecutor(indexer).execute_with_diagnostics(document, [rule])

    assert report.diagnostics[0].status == "success"
    assert report.results[0].value == "14:30"
    assert report.results[0].bbox_source == "table_cell"


def test_rule_executor_extracts_table_when_keywords_are_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    table = SimpleNamespace(
        bbox=(50, 100, 250, 160),
        extract=lambda: [["Item", "Amount"], ["Net income", "5,955"]],
    )
    page = SimpleNamespace(height=400, find_tables=lambda **_kwargs: [table])
    monkeypatch.setattr(
        "pdf_extractor.extractor.table_extractor.pdfplumber.open",
        lambda _path: nullcontext(SimpleNamespace(pages=[page])),
    )
    paragraph = Paragraph("p_1", "Income statement", 1, BBox(50, 50, 350, 80))
    document = Document("sample.pdf", [Page(1, 400, 400, [paragraph])])
    rule = ExtractionRule(
        "income_table",
        "Income table",
        None,
        [],
        "table",
        "Income table",
        within_heading="Income statement",
    )
    with FTSIndexer() as indexer:
        indexer.build(document)

        report = RuleExecutor(indexer).execute_with_diagnostics(document, [rule])

    assert report.diagnostics[0].status == "success"
    assert report.results[0].value == [["Item", "Amount"], ["Net income", "5,955"]]


def test_rule_executor_reports_table_row_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    table = SimpleNamespace(
        bbox=(50, 100, 250, 160),
        extract=lambda: [["Item", "Amount"], ["Net income", "5,955"]],
    )
    page = SimpleNamespace(height=400, find_tables=lambda **_kwargs: [table])
    monkeypatch.setattr(
        "pdf_extractor.extractor.table_extractor.pdfplumber.open",
        lambda _path: nullcontext(SimpleNamespace(pages=[page])),
    )
    paragraph = Paragraph("p_1", "Income table", 1, BBox(50, 50, 350, 80))
    document = Document("sample.pdf", [Page(1, 400, 400, [paragraph])])
    rule = ExtractionRule(
        "missing_cell",
        "Missing cell",
        None,
        ["Income table"],
        "number",
        "Missing amount",
        table_selector={"row_header": "Operating profit", "column_header": "Amount"},
    )
    with FTSIndexer() as indexer:
        indexer.build(document)

        report = RuleExecutor(indexer).execute_with_diagnostics(document, [rule])

    assert report.results == []
    assert report.diagnostics[0].status == "table_row_not_found"
    assert "row" in report.diagnostics[0].message


def test_rule_executor_reports_table_column_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    table = SimpleNamespace(
        bbox=(50, 100, 250, 160),
        extract=lambda: [["Item", "Amount"], ["Net income", "5,955"]],
    )
    page = SimpleNamespace(height=400, find_tables=lambda **_kwargs: [table])
    monkeypatch.setattr(
        "pdf_extractor.extractor.table_extractor.pdfplumber.open",
        lambda _path: nullcontext(SimpleNamespace(pages=[page])),
    )
    paragraph = Paragraph("p_1", "Income table", 1, BBox(50, 50, 350, 80))
    document = Document("sample.pdf", [Page(1, 400, 400, [paragraph])])
    rule = ExtractionRule(
        "missing_column",
        "Missing column",
        None,
        ["Income table"],
        "number",
        "Missing amount",
        table_selector={"row_header": "Net income", "column_header": "YoY"},
    )
    with FTSIndexer() as indexer:
        indexer.build(document)

        report = RuleExecutor(indexer).execute_with_diagnostics(document, [rule])

    assert report.results == []
    assert report.diagnostics[0].status == "table_column_not_found"
    assert "column" in report.diagnostics[0].message


def test_rule_executor_reports_table_cell_type_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    table = SimpleNamespace(
        bbox=(50, 100, 250, 160),
        extract=lambda: [["Item", "Amount"], ["Net income", "N/A"]],
    )
    page = SimpleNamespace(height=400, find_tables=lambda **_kwargs: [table])
    monkeypatch.setattr(
        "pdf_extractor.extractor.table_extractor.pdfplumber.open",
        lambda _path: nullcontext(SimpleNamespace(pages=[page])),
    )
    paragraph = Paragraph("p_1", "Income table", 1, BBox(50, 50, 350, 80))
    document = Document("sample.pdf", [Page(1, 400, 400, [paragraph])])
    rule = ExtractionRule(
        "wrong_type",
        "Wrong type",
        None,
        ["Income table"],
        "number",
        "Amount",
        table_selector={"row_header": "Net income", "column_header": "Amount"},
    )
    with FTSIndexer() as indexer:
        indexer.build(document)

        report = RuleExecutor(indexer).execute_with_diagnostics(document, [rule])

    assert report.results == []
    assert report.diagnostics[0].status == "table_cell_type_not_found"


def test_rule_executor_resolves_scope_and_dispatches_value_extractor() -> None:
    document = _document()
    with FTSIndexer() as indexer:
        indexer.build(document)

        results = RuleExecutor(indexer).execute(
            document,
            [_rule(scope="第一章 第三节 财务表现")],
        )

    assert results[0].value == "12.5 亿元"
    assert results[0].section_title == "第三节 财务表现"


def test_rule_executor_skips_unknown_scope() -> None:
    document = _document()
    with FTSIndexer() as indexer:
        indexer.build(document)

        results = RuleExecutor(indexer).execute(document, [_rule(scope="不存在的章节")])

    assert results == []


def test_rule_executor_reports_scope_not_found() -> None:
    document = _document()
    with FTSIndexer() as indexer:
        indexer.build(document)

        report = RuleExecutor(indexer).execute_with_diagnostics(
            document, [_rule(scope="不存在的章节")]
        )

    assert report.results == []
    assert report.diagnostics[0].status == "scope_not_found"


def test_rule_executor_resolves_unique_shorthand_scope() -> None:
    document = _document()
    with FTSIndexer() as indexer:
        indexer.build(document)

        report = RuleExecutor(indexer).execute_with_diagnostics(
            document, [_rule(scope="第三节 财务表现")]
        )

    assert report.diagnostics[0].status == "success"


@pytest.mark.parametrize(
    ("section_title", "scope"),
    [
        ("  2025 FINANCIAL STATEMENTS  ", "2025 FINANCIAL STATEMENTS"),
        ("2025 FINANCIAL\u00a0STATEMENTS", "2025 FINANCIAL STATEMENTS"),
        ("\u200b2025 FINANCIAL STATEMENTS\u200b", "2025 FINANCIAL STATEMENTS"),
        ("2025 FINANCIAL STATEMENTS", "  2025 FINANCIAL STATEMENTS  "),
    ],
)
def test_rule_executor_ignores_pdf_title_whitespace_in_scope(
    section_title: str,
    scope: str,
) -> None:
    paragraph = Paragraph(
        "p_1",
        "公司净收入为 12.5 亿元。",
        1,
        BBox(10, 20, 200, 40),
        "s_1",
    )
    document = Document(
        "sample.pdf",
        [Page(1, 300, 400, [paragraph])],
        [Section("s_1", section_title, 1, 1, 1, ["p_1"])],
    )
    with FTSIndexer() as indexer:
        indexer.build(document)

        report = RuleExecutor(indexer).execute_with_diagnostics(
            document,
            [_rule(scope=scope)],
        )

    assert report.diagnostics[0].status == "success"
    assert report.results[0].section_title == section_title


def test_rule_executor_reports_ambiguous_shorthand_scope() -> None:
    paragraph = Paragraph("p_1", "净收入为 12.5 亿元。", 2, BBox(0, 0, 10, 10), "s_2")
    sections = [
        Section("s_1", "第一章", 1, 1, 1, path=["第一章"]),
        Section("s_2", "第三节 财务表现", 2, 1, 1, parent_id="s_1", path=["第一章", "第三节 财务表现"]),
        Section("s_3", "第二章", 1, 2, 2, path=["第二章"]),
        Section("s_4", "第三节 财务表现", 2, 2, 2, parent_id="s_3", path=["第二章", "第三节 财务表现"]),
    ]
    document = Document("sample.pdf", [Page(2, 100, 100, [paragraph])], sections)
    with FTSIndexer() as indexer:
        indexer.build(document)

        report = RuleExecutor(indexer).execute_with_diagnostics(
            document, [_rule(scope="第三节 财务表现")]
        )

    assert report.results == []
    assert report.diagnostics[0].status == "scope_ambiguous"


def test_rule_executor_resolves_full_section_path() -> None:
    paragraphs = [
        Paragraph("p_1", "净收入为 10 亿元。", 1, BBox(0, 0, 10, 10), "s_2"),
        Paragraph("p_2", "净收入为 20 亿元。", 2, BBox(0, 0, 10, 10), "s_4"),
    ]
    sections = [
        Section("s_1", "第一章", 1, 1, 1, path=["第一章"]),
        Section("s_2", "第三节 财务表现", 2, 1, 1, ["p_1"], "s_1", ["第一章", "第三节 财务表现"]),
        Section("s_3", "第二章", 1, 2, 2, path=["第二章"]),
        Section("s_4", "第三节 财务表现", 2, 2, 2, ["p_2"], "s_3", ["第二章", "第三节 财务表现"]),
    ]
    document = Document(
        "sample.pdf",
        [Page(1, 100, 100, paragraphs[:1]), Page(2, 100, 100, paragraphs[1:])],
        sections,
    )
    with FTSIndexer() as indexer:
        indexer.build(document)

        results = RuleExecutor(indexer).execute(
            document, [_rule(scope="第二章 > 第三节 财务表现")]
        )

    assert [result.value for result in results] == ["20 亿元"]


def test_rule_executor_filters_candidates_after_within_heading() -> None:
    paragraphs = [
        Paragraph("p_1", "净收入为 10 亿元。", 1, BBox(0, 0, 10, 10), "s_1"),
        Paragraph("p_2", "合并利润表", 3, BBox(0, 0, 10, 10), "s_1"),
        Paragraph("p_3", "净收入为 20 亿元。", 3, BBox(0, 20, 10, 30), "s_1"),
    ]
    sections = [Section("s_1", "财务报表", 1, 1, 3, [p.id for p in paragraphs])]
    document = Document(
        "sample.pdf",
        [Page(1, 100, 100, paragraphs[:1]), Page(3, 100, 100, paragraphs[1:])],
        sections,
    )
    with FTSIndexer() as indexer:
        indexer.build(document)

        results = RuleExecutor(indexer).execute(
            document,
            [_rule(scope="财务报表", within_heading="合并利润表")],
        )

    assert [result.value for result in results] == ["20 亿元"]


def test_rule_executor_uses_heading_fallback_when_scope_boundary_is_too_short() -> None:
    paragraphs = [
        Paragraph("p_1", "净收入为 10 亿元。", 1, BBox(0, 0, 10, 10), "s_1"),
        Paragraph("p_2", "财务报表 合并利润表", 4, BBox(0, 0, 10, 10), "s_2"),
        Paragraph("p_3", "净收入为 20 亿元。", 4, BBox(0, 20, 10, 30), "s_2"),
    ]
    sections = [
        Section("s_1", "财务报表", 1, 1, 1, ["p_1"], path=["财务报表"]),
        Section("s_2", "审计报告", 1, 2, 5, ["p_2", "p_3"], path=["审计报告"]),
    ]
    document = Document(
        "sample.pdf",
        [Page(1, 100, 100, paragraphs[:1]), Page(4, 100, 100, paragraphs[1:])],
        sections,
    )
    with FTSIndexer() as indexer:
        indexer.build(document)

        report = RuleExecutor(indexer).execute_with_diagnostics(
            document,
            [_rule(scope="财务报表", within_heading="合并利润表")],
        )

    assert report.diagnostics[0].status == "success"
    assert [result.value for result in report.results] == ["20 亿元"]


def test_rule_executor_reports_missing_within_heading() -> None:
    document = _document()
    with FTSIndexer() as indexer:
        indexer.build(document)

        report = RuleExecutor(indexer).execute_with_diagnostics(
            document, [_rule(within_heading="不存在的标题")]
        )

    assert report.results == []
    assert report.diagnostics[0].status == "within_heading_not_found"


def test_rule_executor_reports_keywords_not_found() -> None:
    document = _document()
    rule = ExtractionRule("missing", "missing", None, ["不存在"], "value", "金额")
    with FTSIndexer() as indexer:
        indexer.build(document)

        report = RuleExecutor(indexer).execute_with_diagnostics(document, [rule])

    assert report.diagnostics[0].status == "keywords_not_found"


def test_rule_executor_reports_value_not_found() -> None:
    paragraph = Paragraph("p_1", "净收入暂未披露。", 1, BBox(0, 0, 10, 10))
    document = Document("sample.pdf", [Page(1, 100, 100, [paragraph])])
    with FTSIndexer() as indexer:
        indexer.build(document)

        report = RuleExecutor(indexer).execute_with_diagnostics(document, [_rule()])

    assert report.diagnostics[0].status == "value_not_found"
    assert report.diagnostics[0].candidate_count == 1


@pytest.mark.parametrize(
    ("extract_type", "expected_status"),
    [
        ("percentage", "percentage_not_found"),
        ("number", "number_not_found"),
        ("date", "date_not_found"),
        ("time", "time_not_found"),
    ],
)
def test_rule_executor_reports_specific_numeric_not_found_status(
    extract_type: str,
    expected_status: str,
) -> None:
    paragraph = Paragraph("p_1", "净收入暂未披露。", 1, BBox(0, 0, 10, 10))
    document = Document("sample.pdf", [Page(1, 100, 100, [paragraph])])
    with FTSIndexer() as indexer:
        indexer.build(document)

        report = RuleExecutor(indexer).execute_with_diagnostics(
            document, [_rule(extract_type)]
        )

    assert report.diagnostics[0].status == expected_status
    assert report.diagnostics[0].candidate_count == 1


def test_rule_executor_reports_table_not_found(tmp_path: Path) -> None:
    pdf_path = tmp_path / "no-table.pdf"
    pdf = pymupdf.open()
    page = pdf.new_page()
    page.insert_text((72, 72), "净收入暂未披露。", fontsize=11)
    pdf.save(pdf_path)
    pdf.close()
    paragraph = Paragraph("p_1", "净收入暂未披露。", 1, BBox(0, 0, 10, 10))
    document = Document(str(pdf_path), [Page(1, 100, 100, [paragraph])])
    with FTSIndexer() as indexer:
        indexer.build(document)

        report = RuleExecutor(indexer).execute_with_diagnostics(
            document, [_rule("table")]
        )

    assert report.diagnostics[0].status == "table_not_found"


def test_rule_executor_reports_success() -> None:
    document = _document()
    with FTSIndexer() as indexer:
        indexer.build(document)

        report = RuleExecutor(indexer).execute_with_diagnostics(document, [_rule()])

    assert report.diagnostics[0].status == "success"
    assert report.diagnostics[0].result_count == 1


def test_optional_llm_helpers_construct_client_and_reject_unimplemented_extract() -> None:
    client = create_openai_client(api_key="test-key")

    with pytest.raises(NotImplementedError, match="not implemented"):
        LLMExtractor(client).extract(_rule(), _document().paragraphs)


def test_load_dotenv_if_present_sets_missing_environment_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dotenv = tmp_path / ".env"
    dotenv.write_text(
        "OPENAI_API_KEY=from-file\n"
        "OPENAI_BASE_URL=http://localhost:1234/v1\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("OPENAI_API_KEY", "existing")
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)

    load_dotenv_if_present(dotenv)

    assert os.environ["OPENAI_API_KEY"] == "existing"
    assert os.environ["OPENAI_BASE_URL"] == "http://localhost:1234/v1"


def test_configure_logging_accepts_explicit_level() -> None:
    configure_logging(logging.DEBUG)
