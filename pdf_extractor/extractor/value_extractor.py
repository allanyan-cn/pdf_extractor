"""基于正则表达式的简单值提取。

Regex-based simple value extraction.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

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
DATE_BODY = (
    r"(?:"
    r"\b\d{4}[-/.]\d{1,2}[-/.]\d{1,2}\b"
    r"|\b\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4}\b"
    r"|\d{4}年\d{1,2}月(?:\d{1,2}日)?"
    r"|\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)"
    r"[a-z]*\.?\s+\d{1,2},?\s+\d{4}\b"
    r"|\b\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)"
    r"[a-z]*\.?\s+\d{4}\b"
    r")"
)
TIME_BODY = (
    r"(?:"
    r"\b(?:[01]?\d|2[0-3]):[0-5]\d(?:[:：][0-5]\d)?\s*(?:AM|PM|am|pm)?\b"
    r"|\b(?:1[0-2]|0?[1-9])\s*(?:AM|PM|am|pm)\b"
    r"|(?:上午|下午|早上|晚上)?\s*(?:[01]?\d|2[0-3])时(?:[0-5]?\d分)?(?:[0-5]?\d秒)?"
    r")"
)

VALUE_PATTERNS = {
    "date": re.compile(DATE_BODY, re.IGNORECASE),
    "time": re.compile(TIME_BODY, re.IGNORECASE),
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
    re.compile(DATE_BODY, re.IGNORECASE),
)
TIME_PATTERNS = (
    re.compile(TIME_BODY, re.IGNORECASE),
)


@dataclass(frozen=True)
class _Candidate:
    """从段落中识别出的候选值及其字符范围。

    A value candidate detected from a paragraph with character offsets.
    """

    value: str
    kind: str
    start: int
    end: int


class ValueExtractor:
    """从每个段落中提取最匹配规则目标的简单值。

    Extract the best matching simple value from each paragraph.
    """

    def extract(
        self,
        rule: ExtractionRule,
        paragraphs: list[Paragraph],
    ) -> list[ExtractionResult]:
        """返回排序后的候选值，并尽量附带 word 级 span 坐标。

        Return ranked matches with span coordinates when available.
        """
        results: list[ExtractionResult] = []
        seen: set[tuple[int, str]] = set()
        for paragraph in paragraphs:
            candidate = self._select_candidate(rule, paragraph.text)
            if not candidate:
                continue
            value = candidate.value
            key = (paragraph.page_number, value)
            # 中文：同一页重复出现的相同值通常来自表头/续行重复，默认去重。
            # English: Same value repeated on one page is often duplicate layout text; dedupe it.
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
                    normalized_value=self._normalize_candidate_value(candidate, rule),
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
        """从段落文本中选择最适合当前规则的候选值。

        Select the best candidate for the current rule from paragraph text.
        """
        candidates = self._find_candidates(text)
        candidates = self._filter_candidates_for_extract_type(rule.extract_type, candidates)
        if not candidates:
            return None
        preferred_kind = self._preferred_kind(rule)
        keyword_ranges = [
            (position, position + len(keyword))
            for keyword in rule.keywords
            if (position := text.casefold().find(keyword.casefold())) >= 0
        ]
        # 中文：排序优先级依次为类型匹配、噪声惩罚、是否在关键词之后、距离关键词远近、出现顺序。
        # English: Ranking prefers type match, low noise, after-keyword values, proximity, then order.
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
    def _normalize_candidate_value(cls, candidate: _Candidate, rule: ExtractionRule) -> Any:
        """根据 extract_type 返回标准化值；value 类型保持原文语义。

        Return normalized values by extract_type; value keeps raw semantics.
        """
        if rule.extract_type == "value":
            return None
        if candidate.kind in {"amount", "quantity", "number", "percentage"}:
            return cls._normalize_decimal(
                candidate.value,
                parentheses_mode=cls._parentheses_mode(rule),
            )
        if candidate.kind in {"date", "time"}:
            return " ".join(candidate.value.split())
        return candidate.value

    @staticmethod
    def _normalize_decimal(value: str, *, parentheses_mode: str = "negative") -> str | None:
        """净化数字字符串，移除千分位、单位和常见符号。

        Normalize a decimal string by removing separators, units, and common symbols.
        """
        text = " ".join(value.strip().split())
        parenthesized = text.startswith("(") and text.endswith(")")
        negative = parenthesized and parentheses_mode == "negative"
        if parenthesized and parentheses_mode == "preserve":
            return text
        # 中文：先剥离外层括号和单位符号，再用 Decimal 规整科学计数法和小数。
        # English: Strip wrappers/units first, then use Decimal for scientific notation/decimals.
        text = text.strip("() ")
        text = re.sub(CURRENCY, "", text, flags=re.IGNORECASE)
        text = re.sub(r"[%％]", "", text)
        text = re.sub(
            rf"(?:{AMOUNT_UNIT}|{QUANTITY_UNIT}|个百分点|个基点|basis\s+points?|bps?)",
            "",
            text,
            flags=re.IGNORECASE,
        )
        text = text.replace(",", "").strip()
        match = re.search(PLAIN_NUMBER, text, re.IGNORECASE)
        if not match:
            return None
        raw_number = match.group(0)
        try:
            number = Decimal(raw_number)
        except InvalidOperation:
            return raw_number
        if negative and number > 0:
            number = -number
        formatted = format(number, "f")
        if "." in formatted:
            return formatted.rstrip("0").rstrip(".")
        return formatted

    @staticmethod
    def _parentheses_mode(rule: ExtractionRule) -> str:
        """读取括号数值标准化模式。

        Read the parentheses normalization mode.
        """
        if not rule.normalization:
            return "negative"
        mode = rule.normalization.get("parentheses", "negative")
        return str(mode)

    @staticmethod
    def _filter_candidates_for_extract_type(
        extract_type: str,
        candidates: list[_Candidate],
    ) -> list[_Candidate]:
        """按 extract_type 过滤候选值。

        Filter candidates according to extract_type.
        """
        if extract_type in {"percentage", "number", "date", "time"}:
            return [candidate for candidate in candidates if candidate.kind == extract_type]
        if extract_type == "value":
            # 中文：value 是泛数值，但刻意排除日期/时间，避免报告期被当成目标数值。
            # English: value is a broad numeric mode but excludes dates/times to avoid period noise.
            return [
                candidate
                for candidate in candidates
                if candidate.kind not in {"date", "time"}
            ]
        return candidates

    @classmethod
    def _find_candidates(cls, text: str) -> list[_Candidate]:
        """用多组正则从文本中召回候选值。

        Recall candidate values from text using regex patterns.
        """
        candidates: list[_Candidate] = []
        occupied: list[tuple[int, int]] = []
        for kind, pattern in VALUE_PATTERNS.items():
            for match in pattern.finditer(text):
                # 中文：先匹配更具体的类型，后续重叠的普通数字不再重复加入。
                # English: Specific patterns win first; overlapping generic numbers are skipped.
                if any(match.start() < end and match.end() > start for start, end in occupied):
                    continue
                candidate = _Candidate(match.group(0).strip(), kind, match.start(), match.end())
                if cls._is_noise(candidate, text):
                    continue
                candidates.append(candidate)
                occupied.append((match.start(), match.end()))
        return candidates

    @staticmethod
    def _preferred_kind(rule: ExtractionRule) -> str:
        """根据规则类型和 target 文本推断优先候选类型。

        Infer the preferred candidate kind from extract_type and target text.
        """
        if rule.extract_type in {"percentage", "number", "date", "time"}:
            return rule.extract_type
        normalized_target = rule.target.casefold()
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
        """给候选值分配噪声惩罚，帮助排序。

        Prefer richer values when the target does not identify a specific type.
        """
        if candidate.kind == "number":
            # 中文：小整数常见于脚注编号或表格 note，低优先级处理。
            # English: Small integers are often footnotes/table notes, so lower their priority.
            raw_number = candidate.value.strip("()+-").replace(",", "")
            if raw_number.isdigit() and int(raw_number) < 100:
                return 2
        return {
            "amount": 0,
            "percentage": 0,
            "quantity": 0,
            "date": 0,
            "time": 0,
            "number": 1,
        }[candidate.kind]

    @staticmethod
    def _is_noise(candidate: _Candidate, text: str) -> bool:
        """判断候选值是否属于日期、年份、脚注等噪声。

        Return whether a candidate is likely date/year/footnote noise.
        """
        if candidate.kind not in {"date", "time"} and any(
            candidate.start < match.end() and candidate.end > match.start()
            for pattern in (*DATE_PATTERNS, *TIME_PATTERNS)
            for match in pattern.finditer(text)
        ):
            return True
        if candidate.kind == "amount":
            raw_value = candidate.value.strip("() ")
            # 中文：如 “2024 $million” 是表头币种单位，不是金额。
            # English: Text such as "2024 $million" is a currency header, not an amount.
            year_currency = re.fullmatch(
                rf"(?:19\d{{2}}|20\d{{2}})\s*{CURRENCY}",
                raw_value,
                re.IGNORECASE,
            )
            if year_currency:
                return True
        if candidate.kind != "number":
            return False
        raw_number = candidate.value.strip("()+-").replace(",", "")
        if raw_number.isdigit() and 1900 <= int(raw_number) <= 2099:
            return True
        before = text[candidate.start - 1 : candidate.start]
        after = text[candidate.end : candidate.end + 1]
        # 中文：嵌在英文单词里的数字通常不是独立业务值。
        # English: Digits embedded in alphabetic tokens are usually not standalone values.
        return before.isalpha() or after.isalpha()

    @staticmethod
    def _keyword_distance(
        candidate: _Candidate, keyword_ranges: list[tuple[int, int]]
    ) -> int:
        """计算候选值到最近关键词的字符距离。

        Compute character distance from the candidate to the nearest keyword.
        """
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
        """当候选值位于关键词之前时增加惩罚。

        Add a penalty when the candidate appears before all keywords.
        """
        if not keyword_ranges:
            return 0
        return 0 if any(candidate.start >= end for _start, end in keyword_ranges) else 1

    @classmethod
    def _span_bbox(cls, paragraph: Paragraph, candidate: _Candidate) -> BBox | None:
        """用 word 坐标合并出候选值的 span bbox。

        Build a candidate span bbox by merging overlapping word boxes.
        """
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
        """把段落 word 映射回原文中的字符范围。

        Map paragraph words back to character ranges in the paragraph text.
        """
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
