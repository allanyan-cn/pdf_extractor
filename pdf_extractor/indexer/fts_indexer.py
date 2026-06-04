"""支持中文关键词的 SQLite FTS5 段落索引。

SQLite FTS5 paragraph index with Chinese keyword support.
"""

from __future__ import annotations

import sqlite3
from collections import Counter
from pathlib import Path

from pdf_extractor.models import Document, Paragraph


class FTSIndexer:
    """构建并检索段落级 SQLite FTS5 trigram 索引。

    Build and search a paragraph-level SQLite FTS5 trigram index.
    """

    def __init__(self, database_path: str | Path = ":memory:") -> None:
        """初始化 SQLite 连接并创建索引结构。

        Initialize the SQLite connection and create the index schema.
        """
        self.connection = sqlite3.connect(str(database_path))
        self._paragraphs: dict[str, Paragraph] = {}
        self._create_schema()

    def _create_schema(self) -> None:
        """创建 FTS5 虚拟表。

        Create the FTS5 virtual table.
        """
        # 中文：trigram tokenizer 可以做中文子串匹配，避免引入中文分词依赖。
        # English: The trigram tokenizer supports Chinese substring matching without CJK tokenizers.
        self.connection.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS paragraphs_fts USING fts5(
                paragraph_id UNINDEXED,
                section_id UNINDEXED,
                page_number UNINDEXED,
                text,
                tokenize = 'trigram'
            )
            """
        )

    def build(self, document: Document) -> None:
        """用当前文档段落替换索引内容。

        Replace the index contents with paragraphs from a document.
        """
        self.connection.execute("DELETE FROM paragraphs_fts")
        self._paragraphs = {paragraph.id: paragraph for paragraph in document.paragraphs}
        self.connection.executemany(
            """
            INSERT INTO paragraphs_fts(paragraph_id, section_id, page_number, text)
            VALUES (?, ?, ?, ?)
            """,
            [
                (
                    paragraph.id,
                    paragraph.section_id,
                    paragraph.page_number,
                    paragraph.text,
                )
                for paragraph in document.paragraphs
            ],
        )
        self.connection.commit()

    def search(
        self,
        keywords: list[str],
        section_id: str | None = None,
        limit: int = 20,
    ) -> list[Paragraph]:
        """返回按关键词命中数量排序的段落。

        Return paragraphs ordered by the number of matching keywords.
        """
        normalized_keywords = list(
            dict.fromkeys(keyword.strip() for keyword in keywords if keyword.strip())
        )
        if not normalized_keywords or limit <= 0:
            return []

        candidate_counts: Counter[str] = Counter()
        for keyword in normalized_keywords:
            for paragraph_id in self._candidate_ids(keyword, section_id):
                candidate_counts[paragraph_id] += 1

        # 中文：FTS/LIKE 先召回候选，再用 Python 子串校验，避免 tokenizer 或转义造成误判。
        # English: Recall with FTS/LIKE, then verify with Python substrings to avoid tokenizer quirks.
        candidates = [
            self._paragraphs[paragraph_id]
            for paragraph_id in candidate_counts
            if paragraph_id in self._paragraphs
        ]
        verified = [
            paragraph
            for paragraph in candidates
            if any(keyword in paragraph.text for keyword in normalized_keywords)
        ]
        verified.sort(
            key=lambda paragraph: (
                -sum(keyword in paragraph.text for keyword in normalized_keywords),
                paragraph.page_number,
                paragraph.bbox.y0,
                paragraph.id,
            )
        )
        return verified[:limit]

    def _candidate_ids(self, keyword: str, section_id: str | None) -> list[str]:
        """为单个关键词召回候选段落 id。

        Return candidate paragraph ids for one keyword.
        """
        if len(keyword) >= 3:
            # 中文：三字及以上关键词走 FTS MATCH，并用双引号包裹为短语查询。
            # English: Keywords of length >= 3 use FTS MATCH wrapped as phrase queries.
            query = '"{}"'.format(keyword.replace('"', '""'))
            sql = "SELECT paragraph_id FROM paragraphs_fts WHERE paragraphs_fts MATCH ?"
            parameters: list[str] = [query]
        else:
            # 中文：trigram 无法稳定匹配少于 3 个字符的查询，短词使用 LIKE fallback。
            # English: Trigram cannot reliably match fewer than 3 chars, so short terms use LIKE.
            escaped_keyword = (
                keyword.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            )
            sql = "SELECT paragraph_id FROM paragraphs_fts WHERE text LIKE ? ESCAPE '\\'"
            parameters = [f"%{escaped_keyword}%"]
        if section_id is not None:
            sql += " AND section_id = ?"
            parameters.append(section_id)
        return [
            str(row[0])
            for row in self.connection.execute(sql, parameters).fetchall()
        ]

    def close(self) -> None:
        """关闭底层 SQLite 连接。

        Close the underlying SQLite connection.
        """
        self.connection.close()

    def __enter__(self) -> FTSIndexer:
        """进入上下文管理器并返回自身。

        Enter the context manager and return self.
        """
        return self

    def __exit__(self, *_args: object) -> None:
        """退出上下文管理器时关闭连接。

        Close the connection when leaving the context manager.
        """
        self.close()
