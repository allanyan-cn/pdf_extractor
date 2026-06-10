"""基于目录和启发式规则的章节识别。

TOC-based and heuristic section detection.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from pdf_extractor.models import Page, Paragraph, Section
from pdf_extractor.utils.text import normalize_match_text, strip_footnote_markers

HEADING_PATTERN = re.compile(
    r"^(?:"
    r"\d+(?:\.\d+)*[.)]?\s+\S+"
    r"|第[一二三四五六七八九十百千零〇两\d]+[章节篇部]\s*\S*"
    r"|Chapter\s+\d+\b.*"
    r"|Section\s+\d+\b.*"
    r")$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class _HeadingCandidate:
    """启发式识别出的标题候选。

    A heading candidate detected by heuristics.
    """

    title: str
    page_number: int
    y0: float
    level: int


class SectionDetector:
    """从 PDF TOC 或简单标题启发式规则识别章节。

    Detect sections from the TOC or simple heading heuristics.
    """

    def detect(self, pdf_doc: Any, pages: list[Page]) -> list[Section]:
        """返回识别出的章节，并把段落关联到对应章节。

        Return detected sections and assign paragraphs to them.
        """
        toc = pdf_doc.get_toc(simple=False)
        if toc:
            # 中文：优先信任 PDF 自带目录，因为它通常包含层级和目标页信息。
            # English: Prefer the PDF TOC because it usually carries hierarchy and targets.
            toc = [item for item in toc if str(item[1]).strip()]
            sections = self._from_toc(toc, len(pages))
            self._assign_from_toc(pages, sections, toc)
            return sections

        # 中文：无 TOC 时退回到字号、位置和标题模式的轻量启发式识别。
        # English: Without a TOC, fall back to lightweight font/position/title heuristics.
        candidates = self._find_heading_candidates(pdf_doc, pages)
        sections = self._from_candidates(candidates, len(pages))
        self._assign_by_heading_position(pages, sections, candidates)
        return sections

    def _from_toc(self, toc: list[list[Any]], page_count: int) -> list[Section]:
        """从 PDF TOC 条目构建 Section 列表。

        Build sections from PDF TOC entries.
        """
        sections = [
            Section(
                id=f"s_{index:04d}",
                title=strip_footnote_markers(str(title)),
                level=int(level),
                start_page=max(1, min(int(page_number), page_count)),
            )
            for index, (level, title, page_number, *_rest) in enumerate(toc, start=1)
            if str(title).strip()
        ]
        self._set_end_pages(sections, page_count)
        self._assign_hierarchy(sections)
        return sections

    def _find_heading_candidates(
        self, pdf_doc: Any, pages: list[Page]
    ) -> list[_HeadingCandidate]:
        """使用字号、位置和标题模式寻找章节标题候选。

        Find heading candidates using font size, page position, and title patterns.
        """
        candidates: list[_HeadingCandidate] = []
        for page, pdf_page in zip(pages, pdf_doc, strict=True):
            body_size, spans = self._page_spans(pdf_page)
            for paragraph in page.paragraphs:
                font_size = max(
                    (
                        size
                        for text, size, bbox in spans
                        if text and paragraph.text in text and bbox[1] >= paragraph.bbox.y0 - 1
                    ),
                    default=0.0,
                )
                is_pattern = bool(HEADING_PATTERN.match(paragraph.text))
                is_short = len(paragraph.text) <= 80
                is_near_top = paragraph.bbox.y0 <= page.height * 0.35
                is_large = font_size > body_size * 1.15 if body_size else False
                # 中文：标题通常较短，并且要么匹配明确章节模式，要么位于页首且字号明显更大。
                # English: Headings are usually short and either pattern-matched or top/large.
                if is_short and (is_pattern or (is_near_top and is_large)):
                    candidates.append(
                        _HeadingCandidate(
                            strip_footnote_markers(paragraph.text),
                            page.page_number,
                            paragraph.bbox.y0,
                            self._infer_level(paragraph.text),
                        )
                    )
        return candidates

    @staticmethod
    def _page_spans(pdf_page: Any) -> tuple[float, list[tuple[str, float, tuple[float, ...]]]]:
        """提取页面 span 及正文参考字号。

        Extract page spans and the reference body font size.
        """
        spans: list[tuple[str, float, tuple[float, ...]]] = []
        for block in pdf_page.get_text("dict").get("blocks", []):
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    text = " ".join(span.get("text", "").split())
                    if text:
                        spans.append((text, float(span.get("size", 0.0)), tuple(span["bbox"])))
        body_size = sorted(size for _text, size, _bbox in spans)[len(spans) // 2] if spans else 0.0
        return body_size, spans

    @staticmethod
    def _infer_level(title: str) -> int:
        """根据标题文本推断章节层级。

        Infer a section level from the heading text.
        """
        numeric = re.match(r"^(\d+(?:\.\d+)*)", title)
        if numeric:
            return numeric.group(1).count(".") + 1
        if re.match(r"^第[一二三四五六七八九十百千零〇两\d]+节", title):
            return 2
        if re.match(r"^Section\s+", title, re.IGNORECASE):
            return 2
        return 1

    def _from_candidates(
        self, candidates: list[_HeadingCandidate], page_count: int
    ) -> list[Section]:
        """从启发式标题候选构建 Section 列表。

        Build sections from heuristic heading candidates.
        """
        sections = [
            Section(
                id=f"s_{index:04d}",
                title=candidate.title,
                level=candidate.level,
                start_page=candidate.page_number,
            )
            for index, candidate in enumerate(candidates, start=1)
        ]
        self._set_end_pages(sections, page_count)
        self._assign_hierarchy(sections)
        return sections

    @staticmethod
    def _set_end_pages(sections: list[Section], page_count: int) -> None:
        """根据下一个章节起始页设置章节结束页。

        Set section end pages from the next section start page.
        """
        for index, section in enumerate(sections):
            next_start = sections[index + 1].start_page if index + 1 < len(sections) else page_count + 1
            section.end_page = max(section.start_page, next_start - 1)

    @staticmethod
    def _assign_hierarchy(sections: list[Section]) -> None:
        """根据章节层级设置 parent_id 和完整路径。

        Assign parent ids and title paths from section levels.
        """
        stack: list[Section] = []
        for section in sections:
            # 中文：栈顶始终保持当前章节的最近上级路径。
            # English: The stack keeps the nearest parent path for the current section.
            while stack and stack[-1].level >= section.level:
                stack.pop()
            parent = stack[-1] if stack else None
            section.parent_id = parent.id if parent else None
            section.path = [*(parent.path if parent else []), section.title]
            stack.append(section)

    @classmethod
    def _assign_from_toc(
        cls,
        pages: list[Page],
        sections: list[Section],
        toc: list[list[Any]],
    ) -> None:
        """根据 TOC 边界把段落分配给章节。

        Assign paragraphs to sections using TOC boundaries.
        """
        paragraphs_by_page = {page.page_number: page.paragraphs for page in pages}
        boundaries = [
            (
                section,
                section.start_page,
                cls._toc_boundary_y(
                    section.title,
                    paragraphs_by_page.get(section.start_page, []),
                    toc_item,
                ),
            )
            for section, toc_item in zip(sections, toc, strict=True)
        ]
        boundaries.sort(key=lambda boundary: (boundary[1], boundary[2]))
        active: Section | None = None
        boundary_index = 0
        # 中文：按页码和 y 坐标扫描段落，遇到新边界就切换当前章节。
        # English: Scan by page/y position and switch the active section at boundaries.
        for page in pages:
            for paragraph in page.paragraphs:
                paragraph_position = (page.page_number, paragraph.bbox.y0)
                while boundary_index < len(boundaries):
                    section, start_page, y0 = boundaries[boundary_index]
                    if (start_page, y0) > paragraph_position:
                        break
                    active = section
                    boundary_index += 1
                if active:
                    paragraph.section_id = active.id
                    active.paragraphs.append(paragraph.id)

    @staticmethod
    def _toc_boundary_y(
        title: str,
        paragraphs: list[Paragraph],
        toc_item: list[Any],
    ) -> float:
        """估算 TOC 目标在起始页上的 y 坐标。

        Estimate the TOC target y coordinate on the start page.
        """
        normalized_title = normalize_match_text(title)
        for paragraph in paragraphs:
            if normalize_match_text(paragraph.text) == normalized_title:
                return paragraph.bbox.y0
        if len(toc_item) >= 4 and isinstance(toc_item[3], dict):
            destination = toc_item[3].get("to")
            if destination is not None and hasattr(destination, "y"):
                return float(destination.y)
        # 中文：无法得到精确位置时回到页顶，保证章节至少从目标页开始。
        # English: Fall back to the page top when no exact destination is available.
        return 0.0

    @staticmethod
    def _assign_by_heading_position(
        pages: list[Page],
        sections: list[Section],
        candidates: list[_HeadingCandidate],
    ) -> None:
        """根据启发式标题位置把段落分配给章节。

        Assign paragraphs to sections using heuristic heading positions.
        """
        boundaries = list(zip(sections, candidates, strict=True))
        active: Section | None = None
        for page in pages:
            for paragraph in page.paragraphs:
                for section, candidate in boundaries:
                    if (
                        candidate.page_number == page.page_number
                        and abs(candidate.y0 - paragraph.bbox.y0) < 1
                        and candidate.title == paragraph.text
                    ):
                        active = section
                        break
                if active:
                    paragraph.section_id = active.id
                    active.paragraphs.append(paragraph.id)
