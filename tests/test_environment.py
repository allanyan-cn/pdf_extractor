"""Verify the baseline runtime capabilities required by the project."""

import sqlite3

import pdf_extractor


def test_package_is_importable() -> None:
    """The package skeleton can be imported before feature work begins."""
    assert pdf_extractor.__doc__


def test_sqlite_fts5_trigram_supports_chinese_keyword_search() -> None:
    """The bundled SQLite build supports the tokenizer required by the indexer."""
    connection = sqlite3.connect(":memory:")
    connection.execute(
        "CREATE VIRTUAL TABLE paragraphs_fts USING fts5(text, tokenize='trigram')"
    )
    connection.execute(
        "INSERT INTO paragraphs_fts(text) VALUES (?)",
        ("公司净收入为 12.5 亿元",),
    )

    matches = connection.execute(
        "SELECT text FROM paragraphs_fts WHERE paragraphs_fts MATCH ?",
        ("净收入",),
    ).fetchall()

    assert matches == [("公司净收入为 12.5 亿元",)]
