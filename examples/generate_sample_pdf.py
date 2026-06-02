"""Generate a small text-based PDF for the README CLI example."""

from __future__ import annotations

from pathlib import Path

import pymupdf


def main() -> None:
    """Write an example PDF containing paragraphs, a TOC, and a simple table."""
    output_path = Path(__file__).with_name("sample.pdf")
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 72), "Chapter 1 Overview", fontsize=18)
    page.insert_text((72, 110), "This report contains a simple financial example.", fontsize=11)
    page.insert_text((72, 170), "Section 2 Results", fontsize=16)
    page.insert_text((72, 205), "Net income reached RMB 3,200 million.", fontsize=11)

    for x in (72, 210, 360):
        page.draw_line((x, 250), (x, 320))
    for y in (250, 285, 320):
        page.draw_line((72, y), (360, y))
    page.insert_text((82, 273), "Item", fontsize=10)
    page.insert_text((220, 273), "Amount", fontsize=10)
    page.insert_text((82, 308), "Net income", fontsize=10)
    page.insert_text((220, 308), "RMB 3,200 million", fontsize=10)

    document.set_toc([[1, "Chapter 1 Overview", 1], [2, "Section 2 Results", 1]])
    document.save(output_path)
    document.close()
    print(f"Generated {output_path}")


if __name__ == "__main__":
    main()
