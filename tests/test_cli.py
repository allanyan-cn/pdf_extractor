"""End-to-end test for the example command-line entry point."""

import json
import subprocess
import sys
from pathlib import Path

import pymupdf


def test_cli_extracts_value_and_writes_json(tmp_path: Path) -> None:
    pdf_path = tmp_path / "sample.pdf"
    rules_path = tmp_path / "rules.json"
    output_path = tmp_path / "output" / "result.json"
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 72), "Section 2 Results", fontsize=16)
    page.insert_text((72, 110), "Net income reached RMB 3,200 million.", fontsize=11)
    document.save(pdf_path)
    document.close()
    rules_path.write_text(
        json.dumps(
            {
                "rules": [
                    {
                        "id": "net_income",
                        "name": "Extract net income",
                        "scope": "Section 2 Results",
                        "keywords": ["Net income"],
                        "extract_type": "value",
                        "target": "Net income amount",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    subprocess.run(
        [
            sys.executable,
            "examples/run_extract.py",
            "--pdf",
            str(pdf_path),
            "--rules",
            str(rules_path),
            "--output",
            str(output_path),
        ],
        check=True,
        cwd=Path(__file__).parents[1],
    )

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["file_path"] == str(pdf_path)
    assert payload["results"][0]["value"] == "RMB 3,200 million"
    assert payload["results"][0]["page_number"] == 1
    assert payload["results"][0]["section_title"] == "Section 2 Results"
    assert payload["results"][0]["section_path"] == ["Section 2 Results"]
    assert payload["results"][0]["bbox_source"] == "span"
    assert payload["diagnostics"][0]["status"] == "success"


def test_cli_returns_two_when_all_rules_have_no_results(tmp_path: Path) -> None:
    pdf_path = tmp_path / "sample.pdf"
    rules_path = tmp_path / "rules.json"
    output_path = tmp_path / "output.json"
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 72), "Section 2 Results", fontsize=16)
    page.insert_text((72, 110), "Net income is unavailable.", fontsize=11)
    document.save(pdf_path)
    document.close()
    rules_path.write_text(
        json.dumps(
            {
                "rules": [
                    {
                        "id": "net_income",
                        "name": "Extract net income",
                        "keywords": ["Net income"],
                        "extract_type": "value",
                        "target": "Net income amount",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    completed = subprocess.run(
        [
            sys.executable,
            "examples/run_extract.py",
            "--pdf",
            str(pdf_path),
            "--rules",
            str(rules_path),
            "--output",
            str(output_path),
        ],
        check=False,
        cwd=Path(__file__).parents[1],
    )

    assert completed.returncode == 2
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["results"] == []
    assert payload["diagnostics"][0]["status"] == "value_not_found"


def test_cli_returns_one_for_missing_pdf(tmp_path: Path) -> None:
    rules_path = tmp_path / "rules.json"
    output_path = tmp_path / "output.json"
    rules_path.write_text('{"rules": []}', encoding="utf-8")

    completed = subprocess.run(
        [
            sys.executable,
            "examples/run_extract.py",
            "--pdf",
            str(tmp_path / "missing.pdf"),
            "--rules",
            str(rules_path),
            "--output",
            str(output_path),
        ],
        check=False,
        cwd=Path(__file__).parents[1],
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 1
    assert "PDF file does not exist" in completed.stderr
    assert not output_path.exists()


def test_cli_reports_ambiguous_scope_and_returns_two(tmp_path: Path) -> None:
    pdf_path = tmp_path / "sample.pdf"
    rules_path = tmp_path / "rules.json"
    output_path = tmp_path / "output.json"
    document = pymupdf.open()
    for chapter in ("Chapter 1", "Chapter 2"):
        page = document.new_page()
        page.insert_text((72, 72), chapter, fontsize=18)
        page.insert_text((72, 110), "Section 2 Results", fontsize=16)
        page.insert_text((72, 145), "Net income reached RMB 3,200 million.", fontsize=11)
    document.save(pdf_path)
    document.close()
    rules_path.write_text(
        json.dumps(
            {
                "rules": [
                    {
                        "id": "net_income",
                        "name": "Extract net income",
                        "scope": "Section 2 Results",
                        "keywords": ["Net income"],
                        "extract_type": "value",
                        "target": "Net income amount",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    completed = subprocess.run(
        [
            sys.executable,
            "examples/run_extract.py",
            "--pdf",
            str(pdf_path),
            "--rules",
            str(rules_path),
            "--output",
            str(output_path),
        ],
        check=False,
        cwd=Path(__file__).parents[1],
    )

    assert completed.returncode == 2
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["diagnostics"][0]["status"] == "scope_ambiguous"
