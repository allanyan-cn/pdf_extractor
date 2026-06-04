"""Tests for complex deterministic and multimodal-assisted table extraction."""

import json
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pymupdf
import pytest

from pdf_extractor.extractor.llm_extractor import MultimodalTableLLMExtractor
from pdf_extractor.extractor.table_extractor import TableExtractor
from pdf_extractor.models import BBox, Document, Page, Paragraph
from pdf_extractor.rules.rule_schema import ExtractionRule


def _rule(
    *,
    table_strategy: str = "auto",
    llm_input: str = "page_image",
) -> ExtractionRule:
    return ExtractionRule(
        "table_rule",
        "Extract income table",
        None,
        ["Net income"],
        "table",
        "Income table",
        table_strategy=table_strategy,
        llm_input=llm_input,
    )


def _document(path: str = "sample.pdf", page_count: int = 2) -> Document:
    paragraph = Paragraph("p_1", "Net income table", 1, BBox(10, 10, 100, 30))
    return Document(
        path,
        [Page(number, 600, 800, [paragraph] if number == 1 else []) for number in range(1, page_count + 1)],
    )


def _table(rows: list[list[Any]], bbox: tuple[float, float, float, float]) -> Any:
    return SimpleNamespace(rows=rows, bbox=bbox, extract=lambda: rows)


def _page(
    *,
    bordered: list[Any] | None = None,
    borderless: list[Any] | None = None,
    height: float = 800,
) -> Any:
    def find_tables(table_settings: dict[str, Any] | None = None) -> list[Any]:
        return list(borderless or []) if table_settings else list(bordered or [])

    return SimpleNamespace(height=height, find_tables=find_tables)


def test_table_extractor_merges_cross_page_table_with_repeated_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pages = [
        _page(
            bordered=[
                _table(
                    [["Item", "Amount"], ["Net income", "10"]],
                    (50, 600, 500, 790),
                )
            ]
        ),
        _page(
            bordered=[
                _table(
                    [["Item", "Amount"], ["Profit", "20"]],
                    (50, 20, 500, 180),
                )
            ]
        ),
    ]
    monkeypatch.setattr(
        "pdf_extractor.extractor.table_extractor.pdfplumber.open",
        lambda _path: nullcontext(SimpleNamespace(pages=pages)),
    )

    result = TableExtractor().extract(_rule(), _document(), [_document().paragraphs[0]])[0]

    assert result.value == [["Item", "Amount"], ["Net income", "10"], ["Profit", "20"]]
    assert result.page_numbers == [1, 2]
    assert result.bbox_source == "table_cross_page"
    assert len(result.bboxes or []) == 2


def test_table_extractor_merges_aligned_page_edge_continuation_without_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pages = [
        _page(
            bordered=[
                _table(
                    [["Item", "Amount"], ["Net income", "10"]],
                    (50, 600, 500, 790),
                )
            ]
        ),
        _page(bordered=[_table([["Profit", "20"]], (50, 20, 500, 180))]),
    ]
    monkeypatch.setattr(
        "pdf_extractor.extractor.table_extractor.pdfplumber.open",
        lambda _path: nullcontext(SimpleNamespace(pages=pages)),
    )

    result = TableExtractor().extract(_rule(), _document(), [_document().paragraphs[0]])[0]

    assert result.value[-1] == ["Profit", "20"]
    assert result.page_numbers == [1, 2]


def test_table_extractor_uses_borderless_text_strategy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = _page(
        borderless=[
            _table(
                [["Item", "Amount"], ["Net income", "10"]],
                (50, 50, 500, 180),
            )
        ]
    )
    monkeypatch.setattr(
        "pdf_extractor.extractor.table_extractor.pdfplumber.open",
        lambda _path: nullcontext(SimpleNamespace(pages=[page])),
    )

    result = TableExtractor().extract(
        _rule(), _document(page_count=1), [_document(page_count=1).paragraphs[0]]
    )[0]

    assert result.bbox_source == "table_text"


def test_table_extractor_reads_real_borderless_table(tmp_path: Path) -> None:
    pdf_path = tmp_path / "borderless.pdf"
    pdf = pymupdf.open()
    page = pdf.new_page(width=600, height=800)
    for x, text in ((72, "Item"), (260, "Amount")):
        page.insert_text((x, 100), text, fontsize=11)
    for x, text in ((72, "Net income"), (260, "10")):
        page.insert_text((x, 130), text, fontsize=11)
    for x, text in ((72, "Profit"), (260, "20")):
        page.insert_text((x, 160), text, fontsize=11)
    pdf.save(pdf_path)
    pdf.close()
    document = _document(str(pdf_path), page_count=1)

    result = TableExtractor().extract(_rule(), document, document.paragraphs)[0]

    assert result.value == [["Item", "Amount"], ["Net income", "10"], ["Profit", "20"]]
    assert result.bbox_source == "table_text"


def test_table_extractor_reads_real_cross_page_table(tmp_path: Path) -> None:
    pdf_path = tmp_path / "cross-page.pdf"
    pdf = pymupdf.open()
    for rows in (
        [("Item", "Amount"), ("Net income", "10")],
        [("Item", "Amount"), ("Profit", "20")],
    ):
        page = pdf.new_page(width=600, height=800)
        for x in (50, 250, 500):
            page.draw_line((x, 600), (x, 790))
        for y in (600, 695, 790):
            page.draw_line((50, y), (500, y))
        for y, row in zip((650, 745), rows, strict=True):
            page.insert_text((70, y), row[0], fontsize=11)
            page.insert_text((270, y), row[1], fontsize=11)
    pdf.save(pdf_path)
    pdf.close()
    document = _document(str(pdf_path))

    result = TableExtractor().extract(_rule(), document, document.paragraphs)[0]

    assert result.value == [["Item", "Amount"], ["Net income", "10"], ["Profit", "20"]]
    assert result.page_numbers == [1, 2]
    assert result.bbox_source == "table_cross_page"


def test_table_extractor_repairs_common_merged_cells() -> None:
    rows = [
        ["Region", "Revenue", None],
        ["East", "10", "20"],
        [None, "30", "40"],
    ]

    repaired = TableExtractor._repair_merged_cells(rows)

    assert repaired == [
        ["Region", "Revenue", "Revenue"],
        ["East", "10", "20"],
        ["East", "30", "40"],
    ]


def test_table_extractor_uses_optional_llm_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assistant = SimpleNamespace(
        extract_table=lambda *_args: [["Item", "Amount"], ["Net income", "10"]]
    )
    monkeypatch.setattr(
        "pdf_extractor.extractor.table_extractor.pdfplumber.open",
        lambda _path: nullcontext(SimpleNamespace(pages=[_page()])),
    )
    document = _document(page_count=1)

    result = TableExtractor(llm_assistant=assistant).extract(
        _rule(), document, document.paragraphs
    )[0]

    assert result.bbox_source == "table_llm"
    assert result.confidence == 0.75
    assert result.bbox == document.paragraphs[0].bbox


def test_table_extractor_local_strategy_skips_llm_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assistant = SimpleNamespace(extract_table=lambda *_args: pytest.fail("unexpected LLM call"))
    monkeypatch.setattr(
        "pdf_extractor.extractor.table_extractor.pdfplumber.open",
        lambda _path: nullcontext(SimpleNamespace(pages=[_page()])),
    )
    document = _document(page_count=1)

    results = TableExtractor(llm_assistant=assistant).extract(
        _rule(table_strategy="local"), document, document.paragraphs
    )

    assert results == []


def test_table_extractor_llm_strategy_skips_local_tables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assistant = SimpleNamespace(
        extract_table=lambda *_args: [["Item", "Amount"], ["Net income", "llm"]]
    )
    monkeypatch.setattr(
        "pdf_extractor.extractor.table_extractor.pdfplumber.open",
        lambda _path: pytest.fail("unexpected local table extraction"),
    )
    document = _document(page_count=1)

    result = TableExtractor(llm_assistant=assistant).extract(
        _rule(table_strategy="llm"), document, document.paragraphs
    )[0]

    assert result.value == [["Item", "Amount"], ["Net income", "llm"]]
    assert result.bbox_source == "table_llm"


def test_multimodal_table_llm_extractor_sends_page_image_and_parses_rows(
    tmp_path: Path,
) -> None:
    pdf_path = tmp_path / "page.pdf"
    pdf = pymupdf.open()
    page = pdf.new_page()
    page.insert_text((72, 72), "Net income 10")
    pdf.save(pdf_path)
    pdf.close()
    calls: list[dict[str, Any]] = []

    def create(**kwargs: Any) -> Any:
        calls.append(kwargs)
        return SimpleNamespace(
            output_text=json.dumps({"rows": [["Item", "Amount"], ["Net income", "10"]]})
        )

    client = SimpleNamespace(responses=SimpleNamespace(create=create))
    assistant = MultimodalTableLLMExtractor(client, model="test-model")

    rows = assistant.extract_table(
        _rule(),
        _document(str(pdf_path), page_count=1),
        [1],
        BBox(10, 10, 100, 30),
    )

    assert rows == [["Item", "Amount"], ["Net income", "10"]]
    assert calls[0]["model"] == "test-model"
    content = calls[0]["input"][0]["content"]
    assert content[1]["type"] == "input_image"
    assert content[1]["image_url"].startswith("data:image/png;base64,")
    assert calls[0]["text"]["format"]["type"] == "json_schema"


def test_multimodal_table_llm_extractor_can_send_candidate_text() -> None:
    calls: list[dict[str, Any]] = []

    def create(**kwargs: Any) -> Any:
        calls.append(kwargs)
        return SimpleNamespace(
            output_text=json.dumps({"rows": [["Item", "Amount"], ["Net income", "10"]]})
        )

    client = SimpleNamespace(responses=SimpleNamespace(create=create))
    assistant = MultimodalTableLLMExtractor(client, model="test-model")

    rows = assistant.extract_table(
        _rule(table_strategy="llm", llm_input="text"),
        _document(page_count=1),
        [1],
        BBox(10, 10, 100, 30),
    )

    content = calls[0]["input"][0]["content"]
    assert rows == [["Item", "Amount"], ["Net income", "10"]]
    assert [item["type"] for item in content] == ["input_text", "input_text"]
    assert "[page 1] Net income table" in content[1]["text"]


def test_multimodal_table_llm_extractor_rejects_invalid_rows(tmp_path: Path) -> None:
    pdf_path = tmp_path / "page.pdf"
    pdf = pymupdf.open()
    pdf.new_page()
    pdf.save(pdf_path)
    pdf.close()
    client = SimpleNamespace(
        responses=SimpleNamespace(
            create=lambda **_kwargs: SimpleNamespace(output_text='{"rows": "invalid"}')
        )
    )

    with pytest.raises(ValueError, match="list of row lists"):
        MultimodalTableLLMExtractor(client).extract_table(
            _rule(),
            _document(str(pdf_path), page_count=1),
            [1],
            BBox(10, 10, 100, 30),
        )
