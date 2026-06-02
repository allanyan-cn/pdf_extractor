"""Regex-based numeric value extraction."""

from __future__ import annotations

import re
from dataclasses import dataclass

from pdf_extractor.models import BBox, ExtractionResult, Paragraph, Word
from pdf_extractor.rules.rule_schema import ExtractionRule

PLAIN_NUMBER = r"[-+]?\d[\d,]*(?:\.\d+)?(?:[eE][-+]?\d+)?"
NUMBER = rf"(?:{PLAIN_NUMBER}|\(\s*{PLAIN_NUMBER}\s*\))"
CURRENCY = r"(?:RMB|CNY|USD|EUR|JPY|HKD|GBP|人民币|美元|欧元|日元|港元|英镑|[$€¥£])"
AMOUNT_UNIT = (
    r"(?:万亿元|亿元|百万元|万元|千元|元|万亿|亿(?!股)|万(?!股)|"
    r"trillion|billion|million|thousand|bn|mn|k)"
)
QUANTITY_UNIT = (
    r"(?:万股|千股|股|人|家|件|台|吨|千克|公斤|平方米|"
    r"shares?|units?|people|employees?|tons?|kg|sqm)"
)
AMOUNT_BODY = (
    rf"(?:{CURRENCY}\s*{PLAIN_NUMBER}(?:\s*{AMOUNT_UNIT})?"
    rf"|{PLAIN_NUMBER}\s*{AMOUNT_UNIT}(?:\s*{CURRENCY})?"
    rf"|{PLAIN_NUMBER}\s*{CURRENCY})"
)
PERCENTAGE_BODY = rf"{PLAIN_NUMBER}\s*(?:%|％|个百分点|个基点|basis\s+points?|bps?)"
QUANTITY_BODY = rf"{PLAIN_NUMBER}\s*{QUANTITY_UNIT}"

VALUE_PATTERNS = {
    "amount": re.compile(
        rf"(?:\(\s*{AMOUNT_BODY}\s*\)|{AMOUNT_BODY})",
        re.IGNORECASE,
    ),
    "percentage": re.compile(
        rf"(?:\(\s*{PERCENTAGE_BODY}\s*\)|{PERCENTAGE_BODY})",
        re.IGNORECASE,
    ),
    "quantity": re.compile(
        rf"(?:\(\s*{QUANTITY_BODY}\s*\)|{QUANTITY_BODY})",
        re.IGNORECASE,
    ),
    "number": re.compile(NUMBER, re.IGNORECASE),
}

DATE_PATTERNS = (
    re.compile(r"\b\d{4}[-/.]\d{1,2}[-/.]\d{1,2}\b"),
    re.compile(r"\b\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4}\b"),
    re.compile(r"\d{4}年\d{1,2}月(?:\d{1,2}日)?"),
)


@dataclass(frozen=True)
class _Candidate:
    value: str
    kind: str
    start: int
    end: int


class ValueExtractor:
    """Extract the best matching numeric value from each paragraph."""

    def extract(
        self,
        rule: ExtractionRule,
        paragraphs: list[Paragraph],
    ) -> list[ExtractionResult]:
        """Return ranked numeric matches with span coordinates when available."""
        results: list[ExtractionResult] = []
        seen: set[tuple[int, str]] = set()
        for paragraph in paragraphs:
            candidate = self._select_candidate(rule, paragraph.text)
            if not candidate:
                continue
            value = candidate.value
            key = (paragraph.page_number, value)
            if key in seen:
                continue
            seen.add(key)
            bbox = self._span_bbox(paragraph, candidate)
            results.append(
                ExtractionResult(
                    rule_id=rule.id,
                    rule_name=rule.name,
                    extract_type=rule.extract_type,
                    target=rule.target,
                    value=value,
                    source_text=paragraph.text,
                    page_number=paragraph.page_number,
                    bbox=bbox or paragraph.bbox,
                    paragraph_id=paragraph.id,
                    bbox_source="span" if bbox else "paragraph",
                    confidence=0.9 if bbox else 0.8,
                )
            )
        return results

    def _select_candidate(self, rule: ExtractionRule, text: str) -> _Candidate | None:
        candidates = self._find_candidates(text)
        if not candidates:
            return None
        preferred_kind = self._preferred_kind(rule.target)
        keyword_ranges = [
            (position, position + len(keyword))
            for keyword in rule.keywords
            if (position := text.casefold().find(keyword.casefold())) >= 0
        ]
        return min(
            candidates,
            key=lambda candidate: (
                candidate.kind != preferred_kind,
                self._candidate_penalty(candidate),
                self._keyword_direction_penalty(candidate, keyword_ranges),
                self._keyword_distance(candidate, keyword_ranges),
                candidate.start,
            ),
        )

    @classmethod
    def _find_candidates(cls, text: str) -> list[_Candidate]:
        candidates: list[_Candidate] = []
        occupied: list[tuple[int, int]] = []
        for kind, pattern in VALUE_PATTERNS.items():
            for match in pattern.finditer(text):
                if any(match.start() < end and match.end() > start for start, end in occupied):
                    continue
                candidate = _Candidate(match.group(0).strip(), kind, match.start(), match.end())
                if cls._is_noise(candidate, text):
                    continue
                candidates.append(candidate)
                occupied.append((match.start(), match.end()))
        return candidates

    @staticmethod
    def _preferred_kind(target: str) -> str:
        normalized_target = target.casefold()
        if any(marker in normalized_target for marker in ("%", "百分比", "比例", "增长率", "率")):
            return "percentage"
        if any(
            marker in normalized_target
            for marker in ("金额", "收入", "利润", "资产", "负债", "revenue", "income", "profit", "amount")
        ):
            return "amount"
        if any(
            marker in normalized_target
            for marker in ("数量", "人数", "员工", "股份", "销量", "count", "quantity", "employees", "shares", "units")
        ):
            return "quantity"
        return "number"

    @staticmethod
    def _candidate_penalty(candidate: _Candidate) -> int:
        """Prefer richer values when the target does not identify a specific type."""
        return {
            "amount": 0,
            "percentage": 0,
            "quantity": 0,
            "number": 1,
        }[candidate.kind]

    @staticmethod
    def _is_noise(candidate: _Candidate, text: str) -> bool:
        if any(
            candidate.start < match.end() and candidate.end > match.start()
            for pattern in DATE_PATTERNS
            for match in pattern.finditer(text)
        ):
            return True
        if candidate.kind != "number":
            return False
        raw_number = candidate.value.strip("()+-").replace(",", "")
        if raw_number.isdigit() and 1900 <= int(raw_number) <= 2099:
            return True
        before = text[candidate.start - 1 : candidate.start]
        after = text[candidate.end : candidate.end + 1]
        return before.isalpha() or after.isalpha()

    @staticmethod
    def _keyword_distance(
        candidate: _Candidate, keyword_ranges: list[tuple[int, int]]
    ) -> int:
        if not keyword_ranges:
            return candidate.start
        return min(
            max(start - candidate.end, candidate.start - end, 0)
            for start, end in keyword_ranges
        )

    @staticmethod
    def _keyword_direction_penalty(
        candidate: _Candidate, keyword_ranges: list[tuple[int, int]]
    ) -> int:
        if not keyword_ranges:
            return 0
        return 0 if any(candidate.start >= end for _start, end in keyword_ranges) else 1

    @classmethod
    def _span_bbox(cls, paragraph: Paragraph, candidate: _Candidate) -> BBox | None:
        word_ranges = cls._word_ranges(paragraph.text, paragraph.words)
        matching_words = [
            word
            for word, start, end in word_ranges
            if start < candidate.end and end > candidate.start
        ]
        if not matching_words:
            return None
        return BBox(
            min(word.bbox.x0 for word in matching_words),
            min(word.bbox.y0 for word in matching_words),
            max(word.bbox.x1 for word in matching_words),
            max(word.bbox.y1 for word in matching_words),
        )

    @staticmethod
    def _word_ranges(text: str, words: list[Word]) -> list[tuple[Word, int, int]]:
        ranges: list[tuple[Word, int, int]] = []
        cursor = 0
        for word in words:
            start = text.find(word.text, cursor)
            if start < 0:
                start = text.find(word.text)
            if start < 0:
                continue
            end = start + len(word.text)
            ranges.append((word, start, end))
            cursor = end
        return ranges
