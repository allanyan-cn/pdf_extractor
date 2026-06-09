"""Tests for the non-UI Rule Studio service layer."""

from __future__ import annotations

import json
from pathlib import Path

import pymupdf
import pytest

from pdf_extractor.models import Document, Section
from rule_studio.services import (
    document_tree,
    execute_rules,
    filter_paragraphs,
    load_page_tables,
    materialize_pdf,
    new_rule_payload,
    parse_rules_json,
    parse_uploaded_pdf,
    render_page_png,
    report_payload,
    resolve_tree_selection,
    rule_to_payload,
    rules_json,
    section_label,
    validate_rule_payload,
)


def create_pdf_bytes() -> bytes:
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 72), "Section 1 Results", fontsize=16)
    page.insert_text((72, 110), "Net income reached RMB 3,200 million.", fontsize=11)
    content = document.tobytes()
    document.close()
    return content


def create_table_pdf_bytes() -> bytes:
    document = pymupdf.open()
    page = document.new_page(width=300, height=400)
    for x in (50, 150, 250):
        page.draw_line((x, 50), (x, 110))
    for y in (50, 80, 110):
        page.draw_line((50, y), (250, y))
    page.insert_text((60, 70), "Item", fontsize=10)
    page.insert_text((160, 70), "Amount", fontsize=10)
    page.insert_text((60, 100), "Net income", fontsize=10)
    page.insert_text((160, 100), "12.5 billion", fontsize=10)
    content = document.tobytes()
    document.close()
    return content


def test_rule_payload_round_trip_and_defaults() -> None:
    payload = new_rule_payload(2)
    payload.update(
        {
            "id": "net_income",
            "name": "Extract net income",
            "keywords": ["Net income"],
            "extract_type": "value",
            "target": "Net income amount",
        }
    )

    rule = validate_rule_payload(payload)
    serialized = rule_to_payload(rule)
    loaded = parse_rules_json(rules_json([rule]))

    assert serialized["priority"] == 1
    assert "table_selector" not in serialized
    assert loaded == [rule]


def test_parse_rules_json_rejects_duplicate_ids() -> None:
    payload = new_rule_payload()
    data = json.dumps({"rules": [payload, payload]})

    with pytest.raises(ValueError, match="unique"):
        parse_rules_json(data)


def test_materialize_parse_filter_render_and_execute(tmp_path: Path) -> None:
    content = create_pdf_bytes()
    path = materialize_pdf(content, "../sample.pdf", tmp_path)
    document = parse_uploaded_pdf(content, "sample.pdf", tmp_path)
    rule = validate_rule_payload(
        {
            "id": "net_income",
            "name": "Extract net income",
            "scope": "Section 1 Results",
            "keywords": ["Net income"],
            "extract_type": "value",
            "target": "Net income amount",
        }
    )

    paragraphs = filter_paragraphs(document, "net income")
    report = execute_rules(document, [rule])
    output = report_payload(document, report)
    image = render_page_png(
        document.file_path,
        1,
        [paragraphs[0].bbox.to_dict()],
        scale=1,
    )

    assert path.parent == tmp_path / "pdf_extractor_rule_studio"
    assert path.name.endswith("-sample.pdf")
    assert len(paragraphs) == 1
    assert section_label(document, paragraphs[0].section_id) == "Section 1 Results"
    tree_items, section_ids = document_tree(document)
    assert section_ids == [document.sections[0].id]
    assert tree_items == [
        {
            "label": "Section 1 Results",
            "tag": "p.1",
            "tooltip": "Section 1 Results",
        }
    ]
    assert output["results"][0]["value"] == "RMB 3,200 million"
    assert output["diagnostics"][0]["status"] == "success"
    assert image.startswith(b"\x89PNG")


def test_render_page_rejects_out_of_range_page(tmp_path: Path) -> None:
    path = materialize_pdf(create_pdf_bytes(), "sample.pdf", tmp_path)

    with pytest.raises(ValueError, match="between 1 and 1"):
        render_page_png(str(path), 2)


def test_load_page_tables_returns_rows_headers_and_bbox(tmp_path: Path) -> None:
    path = materialize_pdf(create_table_pdf_bytes(), "table.pdf", tmp_path)

    tables = load_page_tables(str(path), 1)

    assert len(tables) == 1
    assert tables[0].label == "Table 1 · 2 rows × 2 columns"
    assert tables[0].rows[1] == ["Net income", "12.5 billion"]
    assert tables[0].row_headers == ["Net income"]
    assert tables[0].column_headers == ["Item", "Amount"]
    assert tables[0].bbox.to_dict() == {
        "x0": 50.0,
        "y0": 50.0,
        "x1": 250.0,
        "y1": 110.0,
    }


def test_document_tree_preserves_section_hierarchy_and_preorder_ids() -> None:
    document = Document(
        "sample.pdf",
        sections=[
            Section("s1", "Chapter 1", 2, 1, 3, path=["Chapter 1"]),
            Section(
                "s2",
                "Revenue",
                3,
                2,
                2,
                parent_id="s1",
                path=["Chapter 1", "Revenue"],
            ),
        ],
    )

    tree_items, section_ids = document_tree(document)

    assert section_ids == ["s1", "s2"]
    assert tree_items == [
        {
            "label": "Chapter 1",
            "tag": "p.1-3",
            "tooltip": "Chapter 1",
            "children": [
                {
                    "label": "Revenue",
                    "tag": "p.2",
                    "tooltip": "Chapter 1 > Revenue",
                }
            ],
        }
    ]
    assert resolve_tree_selection(document, section_ids, 1) == ("s2", 2)
    assert resolve_tree_selection(document, section_ids, [0, 1]) == ("s2", 2)
    assert resolve_tree_selection(document, section_ids, []) is None
    assert resolve_tree_selection(document, section_ids, 5) is None
