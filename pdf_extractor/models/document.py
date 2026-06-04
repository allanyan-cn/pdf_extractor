"""解析后的 PDF 文档数据模型。

Parsed PDF document data models.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pdf_extractor.utils.bbox import BBox


@dataclass(frozen=True)
class Word:
    """带 PDF 坐标的单词 token。

    A word token with its PDF coordinates.
    """

    text: str
    bbox: BBox


@dataclass
class Paragraph:
    """从 PDF 页面提取出的文本块。

    A text block extracted from a PDF page.
    """

    id: str
    text: str
    page_number: int
    bbox: BBox
    section_id: str | None = None
    words: list[Word] = field(default_factory=list)


@dataclass
class Page:
    """使用 1-based 页码表示的 PDF 页面及其段落。

    A one-based PDF page with paragraph blocks.
    """

    page_number: int
    width: float
    height: float
    paragraphs: list[Paragraph] = field(default_factory=list)


@dataclass
class Section:
    """由 TOC 或标题启发式规则识别出的文档章节。

    A document section derived from the TOC or heading heuristics.
    """

    id: str
    title: str
    level: int
    start_page: int
    end_page: int | None = None
    paragraphs: list[str] = field(default_factory=list)
    parent_id: str | None = None
    path: list[str] = field(default_factory=list)


@dataclass
class Document:
    """文本型 PDF 的解析结果。

    The parsed representation of a text-based PDF file.
    """

    file_path: str
    pages: list[Page] = field(default_factory=list)
    sections: list[Section] = field(default_factory=list)

    @property
    def paragraphs(self) -> list[Paragraph]:
        """按阅读顺序返回全文段落。

        Return document paragraphs in reading order.
        """
        return [
            paragraph
            for page in self.pages
            for paragraph in page.paragraphs
        ]
