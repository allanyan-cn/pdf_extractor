"""SQLite FTS5 paragraph index with Chinese keyword support."""

from __future__ import annotations

import sqlite3
from collections import Counter
from pathlib import Path

from pdf_extractor.models import Document, Paragraph


class FTSIndexer:
    """Build and search a paragraph-level SQLite FTS5 trigram index."""

    def __init__(self, database_path: str | Path = ":memory:") -> None:
        self.connection = sqlite3.connect(str(database_path))
        self._paragraphs: dict[str, Paragraph] = {}
        self._create_schema()

    def _create_schema(self) -> None:
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
        """Replace the index contents with paragraphs from a document."""
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
        """Return paragraphs ordered by the number of matching keywords."""
        normalized_keywords = list(
            dict.fromkeys(keyword.strip() for keyword in keywords if keyword.strip())
        )
        if not normalized_keywords or limit <= 0:
            return []

        candidate_counts: Counter[str] = Counter()
        for keyword in normalized_keywords:
            for paragraph_id in self._candidate_ids(keyword, section_id):
                candidate_counts[paragraph_id] += 1

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
        if len(keyword) >= 3:
            query = '"{}"'.format(keyword.replace('"', '""'))
            sql = "SELECT paragraph_id FROM paragraphs_fts WHERE paragraphs_fts MATCH ?"
            parameters: list[str] = [query]
        else:
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
        """Close the underlying SQLite connection."""
        self.connection.close()

    def __enter__(self) -> FTSIndexer:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()
