"""Tests for the paragraph-level SQLite FTS5 index."""

from pdf_extractor.indexer.fts_indexer import FTSIndexer
from pdf_extractor.models import BBox, Document, Page, Paragraph


def _document() -> Document:
    paragraphs = [
        Paragraph("p_1", "公司净收入为 12.5 亿元，利润明显增长。", 1, BBox(0, 0, 10, 10), "s_1"),
        Paragraph("p_2", "本期利润有所提升。", 1, BBox(0, 20, 10, 30), "s_1"),
        Paragraph("p_3", "第二章记录净收入变化。", 2, BBox(0, 0, 10, 10), "s_2"),
    ]
    return Document(
        "sample.pdf",
        [
            Page(1, 100, 100, paragraphs[:2]),
            Page(2, 100, 100, paragraphs[2:]),
        ],
    )


def test_search_prefers_paragraph_matching_all_keywords() -> None:
    with FTSIndexer() as indexer:
        indexer.build(_document())

        matches = indexer.search(["利润", "净收入"])

    assert [paragraph.id for paragraph in matches] == ["p_1", "p_2", "p_3"]


def test_search_filters_by_section() -> None:
    with FTSIndexer() as indexer:
        indexer.build(_document())

        matches = indexer.search(["净收入"], section_id="s_2")

    assert [paragraph.id for paragraph in matches] == ["p_3"]


def test_search_uses_like_fallback_for_short_keyword() -> None:
    with FTSIndexer() as indexer:
        indexer.build(_document())

        matches = indexer.search(["利润"])

    assert [paragraph.id for paragraph in matches] == ["p_1", "p_2"]


def test_search_returns_paragraph_bbox_and_honors_limit() -> None:
    with FTSIndexer() as indexer:
        indexer.build(_document())

        matches = indexer.search(["净收入"], limit=1)

    assert len(matches) == 1
    assert matches[0].bbox == BBox(0, 0, 10, 10)


def test_build_replaces_previous_document() -> None:
    with FTSIndexer() as indexer:
        indexer.build(_document())
        indexer.build(Document("empty.pdf"))

        assert indexer.search(["净收入"]) == []


def test_search_treats_like_wildcards_as_literal_characters() -> None:
    paragraph = Paragraph("p_1", "利润率为 8_6%。", 1, BBox(0, 0, 10, 10))
    document = Document("sample.pdf", [Page(1, 100, 100, [paragraph])])
    with FTSIndexer() as indexer:
        indexer.build(document)

        matches = indexer.search(["_6"])

    assert [match.id for match in matches] == ["p_1"]


def test_search_returns_empty_list_for_empty_keywords_or_limit() -> None:
    with FTSIndexer() as indexer:
        indexer.build(_document())

        assert indexer.search([]) == []
        assert indexer.search(["净收入"], limit=0) == []
