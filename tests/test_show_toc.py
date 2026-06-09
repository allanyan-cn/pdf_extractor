"""Tests for the PDF table-of-contents inspection utility."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pymupdf

from pdf_extractor.utils.show_toc import format_toc, load_toc, main


def create_pdf(path: Path, toc: list[list[int | str]] | None = None) -> None:
    document = pymupdf.open()
    for title in ("First page", "Second page"):
        page = document.new_page()
        page.insert_text((72, 72), title)
    if toc:
        document.set_toc(toc)
    document.save(path)
    document.close()


def test_load_and_format_complete_toc(tmp_path: Path) -> None:
    pdf_path = tmp_path / "with-toc.pdf"
    create_pdf(
        pdf_path,
        [
            [1, "第一章 实际标题", 1],
            [2, "1.1 Revenue & Profit", 1],
            [2, "1.2 Notes", 2],
            [1, "Appendix", 2],
        ],
    )

    entries = load_toc(pdf_path)

    assert entries == [
        (1, "第一章 实际标题", 1),
        (2, "1.1 Revenue & Profit", 1),
        (2, "1.2 Notes", 2),
        (1, "Appendix", 2),
    ]
    assert format_toc(entries) == (
        "- [第一章 实际标题] (page 1)\n"
        "  - [1.1 Revenue & Profit] (page 1)\n"
        "  - [1.2 Notes] (page 2)\n"
        "- [Appendix] (page 2)"
    )


def test_main_reports_pdf_without_embedded_toc(
    tmp_path: Path, capsys
) -> None:
    pdf_path = tmp_path / "without-toc.pdf"
    create_pdf(pdf_path)

    assert main([str(pdf_path)]) == 0
    assert capsys.readouterr().out == "No embedded table of contents found.\n"


def test_main_returns_one_for_missing_pdf(tmp_path: Path, capsys) -> None:
    missing_path = tmp_path / "missing.pdf"

    assert main([str(missing_path)]) == 1
    assert f"PDF file does not exist: {missing_path}" in capsys.readouterr().err


def test_module_cli_prints_toc(tmp_path: Path) -> None:
    pdf_path = tmp_path / "with-toc.pdf"
    create_pdf(pdf_path, [[1, "Chapter One", 1], [2, "Section A", 2]])

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "pdf_extractor.utils.show_toc",
            str(pdf_path),
        ],
        check=False,
        cwd=Path(__file__).parents[1],
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0
    assert completed.stdout == (
        "- [Chapter One] (page 1)\n"
        "  - [Section A] (page 2)\n"
    )
    assert completed.stderr == ""
