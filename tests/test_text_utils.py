"""Tests for shared PDF text cleanup."""

from pdf_extractor.utils.text import normalize_match_text, strip_footnote_markers


def test_strip_footnote_markers_handles_common_pdf_forms() -> None:
    assert strip_footnote_markers("Dividend payout ratio8") == "Dividend payout ratio"
    assert strip_footnote_markers("Dividends paid in the period)7") == (
        "Dividends paid in the period)"
    )
    assert strip_footnote_markers("20221") == "2022"
    assert strip_footnote_markers("Revenue¹") == "Revenue"
    assert strip_footnote_markers("净收入2") == "净收入"


def test_strip_footnote_markers_preserves_ordinary_numbers() -> None:
    assert strip_footnote_markers("Chapter 2") == "Chapter 2"
    assert strip_footnote_markers("2024") == "2024"
    assert strip_footnote_markers("12.5") == "12.5"
    assert strip_footnote_markers("IFRS17") == "IFRS17"


def test_normalize_match_text_ignores_footnotes_and_spacing() -> None:
    assert normalize_match_text("Financial summary²") == normalize_match_text(
        " Financial summary "
    )
