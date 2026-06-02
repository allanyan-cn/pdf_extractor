"""Rule-driven extraction orchestration."""

from __future__ import annotations

import re
import logging
from dataclasses import dataclass
from typing import Any

from pdf_extractor.extractor.table_extractor import TableExtractor
from pdf_extractor.extractor.text_extractor import TextExtractor
from pdf_extractor.extractor.value_extractor import ValueExtractor
from pdf_extractor.indexer.fts_indexer import FTSIndexer
from pdf_extractor.models import (
    Document,
    ExecutionReport,
    ExtractionResult,
    RuleDiagnostic,
    Section,
)
from pdf_extractor.rules.rule_schema import ExtractionRule

SCOPE_SEPARATOR_PATTERN = re.compile(r"\s*(?:>|/|::)\s*")


@dataclass(frozen=True)
class ScopeResolution:
    """A resolved section id or an explicit scope failure status."""

    section_id: str | None
    status: str
    candidates: list[str]


class RuleExecutor:
    """Locate candidate paragraphs and dispatch rules to extractors."""

    def __init__(
        self,
        indexer: FTSIndexer,
        text_extractor: TextExtractor | None = None,
        value_extractor: ValueExtractor | None = None,
        table_extractor: TableExtractor | None = None,
    ) -> None:
        self.indexer = indexer
        self.extractors: dict[str, Any] = {
            "text": text_extractor or TextExtractor(),
            "value": value_extractor or ValueExtractor(),
            "table": table_extractor or TableExtractor(),
        }

    def execute(
        self,
        document: Document,
        rules: list[ExtractionRule],
    ) -> list[ExtractionResult]:
        """Execute rules and return extracted results for compatibility."""
        return self.execute_with_diagnostics(document, rules).results

    def execute_with_diagnostics(
        self,
        document: Document,
        rules: list[ExtractionRule],
    ) -> ExecutionReport:
        """Execute rules and return extracted results with troubleshooting details."""
        results: list[ExtractionResult] = []
        diagnostics: list[RuleDiagnostic] = []
        sections_by_id = {section.id: section for section in document.sections}
        logger = logging.getLogger(__name__)
        for rule in sorted(rules, key=lambda item: (-item.priority, item.id)):
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
            paragraphs = self.indexer.search(rule.keywords, section_id=section_id)
            if not paragraphs:
                diagnostics.append(
                    RuleDiagnostic(
                        rule.id,
                        "keywords_not_found",
                        "No paragraphs matched the configured keywords.",
                        scope=rule.scope,
                        section_id=section_id,
                    )
                )
                logger.info("Rule %s: no keyword candidates", rule.id)
                continue
            extractor = self.extractors[rule.extract_type]
            if rule.extract_type == "table":
                extracted = extractor.extract(rule, document, paragraphs)
            else:
                extracted = extractor.extract(rule, paragraphs)
            for result in extracted:
                if result.paragraph_id:
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
                        section_id=section_id,
                        candidate_count=len(paragraphs),
                        result_count=len(extracted),
                    )
                )
            else:
                status = (
                    "table_not_found"
                    if rule.extract_type == "table"
                    else "value_not_found"
                    if rule.extract_type == "value"
                    else "text_not_found"
                )
                diagnostics.append(
                    RuleDiagnostic(
                        rule.id,
                        status,
                        f"No {rule.extract_type} results were extracted from the matched paragraphs.",
                        scope=rule.scope,
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

    @staticmethod
    def resolve_scope(document: Document, scope: str | None) -> str | None:
        """Resolve a human-readable scope when it identifies exactly one section."""
        return RuleExecutor.resolve_scope_details(document, scope).section_id

    @staticmethod
    def resolve_scope_details(document: Document, scope: str | None) -> ScopeResolution:
        """Resolve a full path or a unique shorthand without guessing."""
        if not scope:
            return ScopeResolution(None, "resolved", [])
        parts = [
            RuleExecutor._normalize(part)
            for part in SCOPE_SEPARATOR_PATTERN.split(scope)
            if part.strip()
        ]
        if len(parts) > 1:
            candidates = [
                section
                for section in document.sections
                if [RuleExecutor._normalize(part) for part in section.path] == parts
            ]
        else:
            normalized_scope = RuleExecutor._normalize(scope)
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
    def _legacy_scope_candidates(document: Document, scope: str) -> list[Section]:
        """Return containment matches for older callers that use combined labels."""
        normalized_scope = RuleExecutor._normalize(scope)
        return [
            section
            for section in document.sections
            if RuleExecutor._normalize(section.title) in normalized_scope
            or normalized_scope in RuleExecutor._normalize(section.title)
        ]

    @staticmethod
    def _normalize(value: str) -> str:
        return re.sub(r"\s+", "", value).casefold()
