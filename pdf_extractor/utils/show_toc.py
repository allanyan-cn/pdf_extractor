"""Print the embedded table of contents of a PDF file."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

import pymupdf

TOCEntry = tuple[int, str, int]


def load_toc(file_path: str | Path) -> list[TOCEntry]:
    """Return all embedded PDF outline entries as ``(level, title, page)``."""
    path = Path(file_path)
    if not path.is_file():
        raise FileNotFoundError(f"PDF file does not exist: {path}")

    with pymupdf.open(path) as pdf_doc:
        return [
            (int(level), str(title), int(page_number))
            for level, title, page_number in pdf_doc.get_toc()
        ]


def format_toc(entries: Sequence[TOCEntry]) -> str:
    """Format PDF outline entries as an indented tree."""
    if not entries:
        return "No embedded table of contents found."

    lines = []
    for level, title, page_number in entries:
        indentation = "  " * max(level - 1, 0)
        page_label = str(page_number) if page_number >= 1 else "-"
        lines.append(f"{indentation}- [{title}] (page {page_label})")
    return "\n".join(lines)


def build_argument_parser() -> argparse.ArgumentParser:
    """Create the command-line argument parser."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", help="Path to the PDF file.")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Print a PDF's complete embedded table of contents."""
    arguments = build_argument_parser().parse_args(argv)
    try:
        entries = load_toc(arguments.pdf)
    except (FileNotFoundError, OSError, RuntimeError, ValueError) as error:
        print(error, file=sys.stderr)
        return 1

    print(format_toc(entries))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
