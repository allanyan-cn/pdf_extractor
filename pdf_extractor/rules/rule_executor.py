"""规则驱动的提取流程编排。

Rule-driven extraction orchestration.
"""

from __future__ import annotations

import re
import logging
import unicodedata
from dataclasses import dataclass
from typing import Any

from pdf_extractor.extractor.table_extractor import TableExtractor
from pdf_extractor.extractor.table_cell_extractor import TableCellExtractor
from pdf_extractor.extractor.text_extractor import TextExtractor
from pdf_extractor.extractor.value_extractor import ValueExtractor
from pdf_extractor.indexer.fts_indexer import FTSIndexer
from pdf_extractor.models import (
    Document,
    ExecutionReport,
    ExtractionResult,
    Paragraph,
    RuleDiagnostic,
    Section,
)
from pdf_extractor.rules.rule_schema import ExtractionRule

SCOPE_SEPARATOR_PATTERN = re.compile(r"\s*(?:>|/|::)\s*")
WITHIN_HEADING_PAGE_WINDOW = 3


@dataclass(frozen=True)
class ScopeResolution:
    """scope 解析结果，包含章节 id 或明确失败状态。

    A resolved section id or an explicit scope failure status.
    """

    section_id: str | None
    status: str
    candidates: list[str]


@dataclass(frozen=True)
class HeadingResolution:
    """within_heading 解析结果，通常是页面标题或表标题段落锚点。

    A paragraph anchor found from a page heading or table title.
    """

    paragraph: Paragraph | None
    status: str
    candidates: list[str]
    used_scope_filter: bool


class RuleExecutor:
    """定位候选段落，并把规则分发给对应 extractor。

    Locate candidate paragraphs and dispatch rules to extractors.
    """

    def __init__(
        self,
        indexer: FTSIndexer,
        text_extractor: TextExtractor | None = None,
        value_extractor: ValueExtractor | None = None,
        table_extractor: TableExtractor | None = None,
        table_cell_extractor: TableCellExtractor | None = None,
    ) -> None:
        """初始化规则执行器和各类 extractor。

        Initialize the rule executor and extractor dependencies.
        """
        resolved_text_extractor = text_extractor or TextExtractor()
        resolved_value_extractor = value_extractor or ValueExtractor()
        resolved_table_extractor = table_extractor or TableExtractor()
        self.indexer = indexer
        self.extractors: dict[str, Any] = {
            "text": resolved_text_extractor,
            "value": resolved_value_extractor,
            "percentage": resolved_value_extractor,
            "number": resolved_value_extractor,
            "date": resolved_value_extractor,
            "time": resolved_value_extractor,
            "table": resolved_table_extractor,
        }
        self.table_cell_extractor = table_cell_extractor or TableCellExtractor(
            resolved_table_extractor,
            resolved_text_extractor,
            resolved_value_extractor,
        )

    def execute(
        self,
        document: Document,
        rules: list[ExtractionRule],
    ) -> list[ExtractionResult]:
        """执行规则并仅返回结果，保留给兼容旧调用方。

        Execute rules and return extracted results for compatibility.
        """
        return self.execute_with_diagnostics(document, rules).results

    def execute_with_diagnostics(
        self,
        document: Document,
        rules: list[ExtractionRule],
    ) -> ExecutionReport:
        """执行规则，并同时返回结果和诊断信息。

        Execute rules and return extracted results with troubleshooting details.
        """
        results: list[ExtractionResult] = []
        diagnostics: list[RuleDiagnostic] = []
        sections_by_id = {section.id: section for section in document.sections}
        logger = logging.getLogger(__name__)
        for rule in sorted(rules, key=lambda item: (item.priority, item.id)):
            # 中文：定位顺序的第一级是 scope；scope 不明确时不继续猜测。
            # English: Scope is the first locator level; ambiguous scopes stop execution.
            scope_resolution = self.resolve_scope_details(document, rule.scope)
            section_id = scope_resolution.section_id
            if rule.scope and scope_resolution.status != "resolved":
                message = (
                    "The configured scope matched multiple sections. Use a full path separated by ' > '."
                    if scope_resolution.status == "scope_ambiguous"
                    else "The configured scope did not match any detected section."
                )
                diagnostics.append(
                    RuleDiagnostic(
                        rule.id,
                        scope_resolution.status,
                        message,
                        scope=rule.scope,
                        within_heading=rule.within_heading,
                    )
                )
                logger.warning(
                    "Rule %s: %s: %s candidates=%s",
                    rule.id,
                    scope_resolution.status,
                    rule.scope,
                    scope_resolution.candidates,
                )
                continue
            # 中文：第二级是 within_heading，用于在章节内进一步锚定页面标题或表标题。
            # English: within_heading is the second locator level for page/table title anchors.
            heading_resolution = self.resolve_within_heading_details(
                document,
                rule.within_heading,
                section_id=section_id,
                scope_section=sections_by_id.get(section_id) if section_id else None,
            )
            if rule.within_heading and heading_resolution.status != "resolved":
                diagnostics.append(
                    RuleDiagnostic(
                        rule.id,
                        heading_resolution.status,
                        "The configured within_heading did not match any extracted paragraph.",
                        scope=rule.scope,
                        within_heading=rule.within_heading,
                        section_id=section_id,
                    )
                )
                logger.info(
                    "Rule %s: %s: %s",
                    rule.id,
                    heading_resolution.status,
                    rule.within_heading,
                )
                continue

            # 中文：第三级是 keywords；若 keywords 为空，则直接使用 scope/heading 后的候选范围。
            # English: Keywords are the third level; empty keywords use the scope/heading range.
            paragraphs = self._locate_paragraphs(
                document,
                rule,
                section_id,
                heading_resolution,
            )
            if not paragraphs:
                status = "keywords_not_found" if rule.keywords else "location_not_found"
                message = (
                    "No paragraphs matched the configured keywords."
                    if rule.keywords
                    else "No paragraphs remained after applying scope/within_heading."
                )
                diagnostics.append(
                    RuleDiagnostic(
                        rule.id,
                        status,
                        message,
                        scope=rule.scope,
                        within_heading=rule.within_heading,
                        section_id=section_id,
                    )
                )
                logger.info("Rule %s: no keyword candidates", rule.id)
                continue
            cell_status: str | None = None
            cell_message: str | None = None
            extractor = self.extractors[rule.extract_type]
            if rule.table_selector:
                # 中文：第四级是表格定位；table_selector 返回更细粒度的行/列诊断。
                # English: Table selection is the fourth level and returns granular row/column diagnostics.
                cell_report = self.table_cell_extractor.extract_with_diagnostics(
                    rule,
                    document,
                    paragraphs,
                )
                extracted = cell_report.results
                cell_status = cell_report.status
                cell_message = cell_report.message
            elif rule.extract_type == "table":
                extracted = extractor.extract(rule, document, paragraphs)
            else:
                extracted = extractor.extract(rule, paragraphs)
            for result in extracted:
                if result.paragraph_id:
                    # 中文：补充章节来源信息，方便用户核对实际命中的章节路径。
                    # English: Add section metadata so users can audit the matched section path.
                    paragraph = next(
                        (
                            candidate
                            for candidate in paragraphs
                            if candidate.id == result.paragraph_id
                        ),
                        None,
                    )
                    section = sections_by_id.get(paragraph.section_id) if paragraph else None
                    result.section_title = section.title if section else None
                    result.section_path = section.path if section else None
                results.append(result)
            if extracted:
                diagnostics.append(
                    RuleDiagnostic(
                        rule.id,
                        "success",
                        "Extraction completed successfully.",
                        scope=rule.scope,
                        within_heading=rule.within_heading,
                        section_id=section_id,
                        candidate_count=len(paragraphs),
                        result_count=len(extracted),
                    )
                )
            else:
                # 中文：没有结果时按最具体的 extractor 类型生成状态，表格单元格优先使用其诊断。
                # English: On failure, choose the most specific status; table-cell diagnostics win.
                status = (
                    cell_status or "table_cell_not_found"
                    if rule.table_selector
                    else "table_not_found"
                    if rule.extract_type == "table"
                    else "percentage_not_found"
                    if rule.extract_type == "percentage"
                    else "number_not_found"
                    if rule.extract_type == "number"
                    else "date_not_found"
                    if rule.extract_type == "date"
                    else "time_not_found"
                    if rule.extract_type == "time"
                    else "value_not_found"
                    if rule.extract_type == "value"
                    else "text_not_found"
                )
                diagnostics.append(
                    RuleDiagnostic(
                        rule.id,
                        status,
                        cell_message
                        or f"No {rule.extract_type} results were extracted from the matched paragraphs.",
                        scope=rule.scope,
                        within_heading=rule.within_heading,
                        section_id=section_id,
                        candidate_count=len(paragraphs),
                    )
                )
            logger.info(
                "Rule %s: candidates=%d results=%d",
                rule.id,
                len(paragraphs),
                len(extracted),
            )
        return ExecutionReport(results, diagnostics)

    def _locate_paragraphs(
        self,
        document: Document,
        rule: ExtractionRule,
        section_id: str | None,
        heading_resolution: HeadingResolution,
    ) -> list[Paragraph]:
        """按 scope、within_heading 和 keywords 定位候选段落。

        Locate candidate paragraphs by scope, within_heading, and keywords.
        """
        keywords = [keyword for keyword in rule.keywords if keyword.strip()]
        if keywords:
            search_section_id = (
                section_id
                if not rule.within_heading or heading_resolution.used_scope_filter
                else None
            )
            # 中文：有 heading/table_selector 时扩大召回，再在 Python 侧按 heading 位置裁剪。
            # English: Widen recall for heading/table rules, then crop by heading position in Python.
            paragraphs = self.indexer.search(
                keywords,
                section_id=search_section_id,
                limit=200 if rule.within_heading or rule.table_selector else 20,
            )
            if heading_resolution.paragraph:
                paragraphs = self._filter_after_heading(
                    document,
                    paragraphs,
                    heading_resolution.paragraph,
                )
            return paragraphs

        if heading_resolution.paragraph:
            # 中文：无关键词时，heading 后 3 页形成候选窗口。
            # English: Without keywords, the three-page window after heading becomes the candidate range.
            return self._filter_after_heading(
                document,
                document.paragraphs,
                heading_resolution.paragraph,
            )
        if section_id:
            return [
                paragraph
                for paragraph in document.paragraphs
                if paragraph.section_id == section_id
            ]
        return document.paragraphs

    @staticmethod
    def resolve_within_heading(
        document: Document,
        within_heading: str | None,
        section_id: str | None = None,
    ) -> Paragraph | None:
        """把页面标题或表标题锚点解析为段落。

        Resolve a page heading/table title anchor to a paragraph.
        """
        return RuleExecutor.resolve_within_heading_details(
            document,
            within_heading,
            section_id=section_id,
        ).paragraph

    @staticmethod
    def resolve_within_heading_details(
        document: Document,
        within_heading: str | None,
        section_id: str | None = None,
        scope_section: Section | None = None,
    ) -> HeadingResolution:
        """查找最合适的页面标题或表标题段落锚点。

        Find the best paragraph anchor for a page heading or table title.
        """
        if not within_heading:
            return HeadingResolution(None, "resolved", [], bool(section_id))

        scoped_paragraphs = (
            [
                paragraph
                for paragraph in document.paragraphs
                if paragraph.section_id == section_id
            ]
            if section_id
            else []
        )
        candidates = RuleExecutor._heading_candidates(scoped_paragraphs, within_heading)
        if candidates and RuleExecutor._heading_match_rank(candidates[0], within_heading) <= 1:
            return HeadingResolution(candidates[0], "resolved", [p.id for p in candidates], True)

        # 中文：如果 TOC 章节边界过窄，允许从 scope 起始页之后继续寻找 heading。
        # English: If a TOC boundary is too narrow, search after the scope start page as fallback.
        search_paragraphs = document.paragraphs
        if scope_section:
            search_paragraphs = [
                paragraph
                for paragraph in document.paragraphs
                if paragraph.page_number >= scope_section.start_page
            ]
        candidates = RuleExecutor._heading_candidates(
            search_paragraphs,
            within_heading,
            scope_section=scope_section,
        )
        if candidates:
            return HeadingResolution(
                candidates[0],
                "resolved",
                [p.id for p in candidates],
                bool(section_id and candidates[0].section_id == section_id),
            )
        return HeadingResolution(None, "within_heading_not_found", [], bool(section_id))

    @staticmethod
    def resolve_scope(document: Document, scope: str | None) -> str | None:
        """当 scope 唯一匹配章节时返回 section_id。

        Resolve a human-readable scope when it identifies exactly one section.
        """
        return RuleExecutor.resolve_scope_details(document, scope).section_id

    @staticmethod
    def resolve_scope_details(document: Document, scope: str | None) -> ScopeResolution:
        """解析完整路径或唯一简写，不做猜测。

        Resolve a full path or a unique shorthand without guessing.
        """
        if not scope:
            return ScopeResolution(None, "resolved", [])
        parts = [
            RuleExecutor._normalize(part)
            for part in SCOPE_SEPARATOR_PATTERN.split(scope)
            if part.strip()
        ]
        if len(parts) > 1:
            # 中文：带分隔符的 scope 必须与 section.path 完整匹配。
            # English: A separated scope must match the full section.path exactly.
            candidates = [
                section
                for section in document.sections
                if [RuleExecutor._normalize(part) for part in section.path] == parts
            ]
        else:
            normalized_scope = RuleExecutor._normalize(scope)
            # 中文：单段 scope 先尝试精确标题或完整路径匹配。
            # English: Single-part scope first tries exact title or full-path matching.
            candidates = [
                section
                for section in document.sections
                if RuleExecutor._normalize(section.title) == normalized_scope
                or (
                    section.path
                    and RuleExecutor._normalize(" > ".join(section.path)) == normalized_scope
                )
            ]
            if not candidates:
                # 中文：兼容早期“组合标签”写法，但仍只接受最深且唯一的候选。
                # English: Keep legacy containment matching, but only accept deepest unique candidates.
                legacy_candidates = RuleExecutor._legacy_scope_candidates(document, scope)
                if legacy_candidates:
                    deepest_level = max(section.level for section in legacy_candidates)
                    candidates = [
                        section
                        for section in legacy_candidates
                        if section.level == deepest_level
                    ]
        if len(candidates) == 1:
            return ScopeResolution(candidates[0].id, "resolved", [candidates[0].id])
        if len(candidates) > 1:
            return ScopeResolution(
                None,
                "scope_ambiguous",
                [section.id for section in candidates],
            )
        return ScopeResolution(None, "scope_not_found", [])

    @staticmethod
    def _filter_after_heading(
        document: Document,
        paragraphs: list[Paragraph],
        heading: Paragraph,
    ) -> list[Paragraph]:
        """保留 heading 之后固定页窗内的段落。

        Keep paragraphs in the fixed page window after the heading.
        """
        paragraph_order = {
            paragraph.id: index for index, paragraph in enumerate(document.paragraphs)
        }
        heading_order = paragraph_order.get(heading.id, -1)
        max_page = heading.page_number + WITHIN_HEADING_PAGE_WINDOW - 1
        return [
            paragraph
            for paragraph in paragraphs
            if paragraph_order.get(paragraph.id, -1) >= heading_order
            and heading.page_number <= paragraph.page_number <= max_page
        ]

    @staticmethod
    def _heading_candidates(
        paragraphs: list[Paragraph],
        heading: str,
        scope_section: Section | None = None,
    ) -> list[Paragraph]:
        """返回包含 heading 文本的候选段落，并按匹配质量排序。

        Return paragraphs containing heading text ordered by match quality.
        """
        normalized_heading = RuleExecutor._normalize(heading)
        if not normalized_heading:
            return []

        def score(paragraph: Paragraph) -> tuple[int, int, float, str]:
            """给 heading 候选排序：匹配越精确、越靠近 scope 起点、越靠上越优先。

            Rank heading candidates by exactness, scope proximity, and vertical position.
            """
            match_rank = RuleExecutor._heading_match_rank(paragraph, heading)
            page_distance = (
                abs(paragraph.page_number - scope_section.start_page)
                if scope_section
                else paragraph.page_number
            )
            return (match_rank, page_distance, paragraph.bbox.y0, paragraph.id)

        candidates = [
            paragraph
            for paragraph in paragraphs
            if normalized_heading in RuleExecutor._normalize(paragraph.text)
        ]
        return sorted(candidates, key=score)

    @staticmethod
    def _heading_match_rank(paragraph: Paragraph, heading: str) -> int:
        """计算 heading 匹配等级，数值越小越精确。

        Return a heading match rank where smaller is more exact.
        """
        normalized_heading = RuleExecutor._normalize(heading)
        normalized_text = RuleExecutor._normalize(paragraph.text)
        if normalized_text == normalized_heading:
            return 0
        if (
            normalized_text.startswith(normalized_heading)
            or normalized_text.endswith(normalized_heading)
        ):
            return 1
        return 2

    @staticmethod
    def _legacy_scope_candidates(document: Document, scope: str) -> list[Section]:
        """返回兼容旧规则组合标签的包含式 scope 候选。

        Return containment matches for older callers that use combined labels.
        """
        normalized_scope = RuleExecutor._normalize(scope)
        return [
            section
            for section in document.sections
            if RuleExecutor._normalize(section.title) in normalized_scope
            or normalized_scope in RuleExecutor._normalize(section.title)
        ]

    @staticmethod
    def _normalize(value: str) -> str:
        """移除 PDF 标题中的空白/不可见字符并大小写折叠。

        Remove whitespace/invisible PDF title characters and case-fold for comparisons.
        """
        normalized = unicodedata.normalize("NFKC", value)
        return "".join(
            character
            for character in normalized
            if not character.isspace()
            and unicodedata.category(character) not in {"Zl", "Zp", "Zs", "Cf"}
        ).casefold()
