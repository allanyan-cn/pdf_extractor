"""PDF text cleanup helpers shared by parsers and extractors."""

from __future__ import annotations

import re
import unicodedata

SUPERSCRIPT_FOOTNOTE_PATTERN = re.compile(r"[⁰¹²³⁴⁵⁶⁷⁸⁹]+")
YEAR_FOOTNOTE_PATTERN = re.compile(
    r"\b((?:19|20)\d{2})[1-9](?=$|[\s,.;:)\]])"
)
ATTACHED_FOOTNOTE_PATTERN = re.compile(
    r"(?<=[a-z\u3400-\u9fff\)])\d{1,2}(?=$|[\s,.;:)\]])"
)
FOOTNOTE_SYMBOL_PATTERN = re.compile(r"[†‡]+(?=$|[\s,.;:)\]])")


def strip_footnote_markers(value: str) -> str:
    """Remove common PDF footnote suffixes without changing ordinary numbers."""
    cleaned = SUPERSCRIPT_FOOTNOTE_PATTERN.sub("", value)
    cleaned = YEAR_FOOTNOTE_PATTERN.sub(r"\1", cleaned)
    cleaned = ATTACHED_FOOTNOTE_PATTERN.sub("", cleaned)
    cleaned = FOOTNOTE_SYMBOL_PATTERN.sub("", cleaned)
    return " ".join(cleaned.split())


def normalize_match_text(value: str) -> str:
    """Normalize cleaned PDF text for title and heading comparisons."""
    normalized = unicodedata.normalize("NFKC", strip_footnote_markers(value))
    return "".join(
        character
        for character in normalized
        if not character.isspace()
        and unicodedata.category(character) not in {"Zl", "Zp", "Zs", "Cf"}
    ).casefold()
