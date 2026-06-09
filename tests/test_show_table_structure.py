"""Tests for the PDF table-header inspection utility."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pymupdf

from pdf_extractor.utils.show_table_structure import (
    TableStructure,
    format_table_structures,
    load_table_structures,
    main,
)


def create_table_pdf(path: Path) -> None:
    document = pymupdf.open()
    page = document.new_page(width=300, height=200)
    for x in (50, 150, 250):
        page.draw_line((x, 50), (x, 110))
    for y in (50, 80, 110):
        page.draw_line((50, y), (250, y))
    page.insert_text((60, 70), "Item", fontsize=10)
    page.insert_text((160, 70), "Amount", fontsize=10)
    page.insert_text((60, 100), "Net income", fontsize=10)
    page.insert_text((160, 100), "5,955", fontsize=10)
    document.save(path)
    document.close()


def test_load_table_structures_returns_rule_headers(tmp_path: Path) -> None:
    pdf_path = tmp_path / "table.pdf"
    create_table_pdf(pdf_path)

    tables = load_table_structures(pdf_path, 1)

    assert tables == [
        TableStructure(
            table_index=1,
            row_headers=["Net income"],
            column_headers=["Item", "Amount"],
        )
    ]


def test_load_table_structures_prefers_printed_page_label(tmp_path: Path) -> None:
    pdf_path = tmp_path / "labelled.pdf"
    document = pymupdf.open()
    document.new_page(width=300, height=200)
    document.new_page(width=300, height=200)
    page = document.new_page(width=300, height=200)
    for x in (50, 150, 250):
        page.draw_line((x, 50), (x, 110))
    for y in (50, 80, 110):
        page.draw_line((50, y), (250, y))
    page.insert_text((60, 70), "Item", fontsize=10)
    page.insert_text((160, 70), "2024", fontsize=10)
    page.insert_text((60, 100), "Revenue", fontsize=10)
    page.insert_text((160, 100), "100", fontsize=10)
    document.set_page_labels(
        [
            {"startpage": 0, "prefix": "", "firstpagenum": 1, "style": "A"},
            {"startpage": 2, "prefix": "", "firstpagenum": 1, "style": "D"},
        ]
    )
    document.save(pdf_path)
    document.close()

    tables = load_table_structures(pdf_path, 1)

    assert tables[0].row_headers == ["Revenue"]
    assert tables[0].column_headers == ["Item", "2024"]


def test_format_table_structures_only_shows_headers() -> None:
    output = format_table_structures(
        3,
        [
            TableStructure(
                1,
                ["Net income", "Operating profit"],
                ["note", "2024", "2023"],
            )
        ],
    )

    assert output == (
        "Page 3: 1 table(s) found.\n"
        "\n"
        "Table 1\n"
        "  column_headers:\n"
        "    - note\n"
        "    - 2024\n"
        "    - 2023\n"
        "  row_headers:\n"
        "    - Net income\n"
        "    - Operating profit"
    )


def test_main_reports_page_without_tables(tmp_path: Path, capsys) -> None:
    pdf_path = tmp_path / "plain.pdf"
    document = pymupdf.open()
    document.new_page()
    document.save(pdf_path)
    document.close()

    assert main([str(pdf_path), "--page", "1"]) == 0
    assert capsys.readouterr().out == "Page 1: no tables found.\n"


def test_main_rejects_page_outside_pdf(tmp_path: Path, capsys) -> None:
    pdf_path = tmp_path / "one-page.pdf"
    document = pymupdf.open()
    document.new_page()
    document.save(pdf_path)
    document.close()

    assert main([str(pdf_path), "--page", "2"]) == 1
    assert "outside the PDF page range 1-1" in capsys.readouterr().err


def test_module_cli_prints_only_table_headers(tmp_path: Path) -> None:
    pdf_path = tmp_path / "table.pdf"
    create_table_pdf(pdf_path)

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "pdf_extractor.utils.show_table_structure",
            str(pdf_path),
            "--page",
            "1",
        ],
        check=False,
        cwd=Path(__file__).parents[1],
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0
    assert "column_headers:" in completed.stdout
    assert "row_headers:" in completed.stdout
    assert "    - Net income" in completed.stdout
    assert "bbox:" not in completed.stdout
    assert "method:" not in completed.stdout
    assert completed.stderr == ""
