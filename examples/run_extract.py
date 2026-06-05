"""Command-line entry point for rule-driven PDF extraction."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from pdf_extractor.indexer.fts_indexer import FTSIndexer
from pdf_extractor.extractor.llm_extractor import MultimodalTableLLMExtractor
from pdf_extractor.extractor.table_extractor import TableExtractor
from pdf_extractor.parser.pdf_parser import PDFParser
from pdf_extractor.rules.rule_executor import RuleExecutor
from pdf_extractor.rules.rule_loader import RuleLoader
from pdf_extractor.utils.logging import configure_logging
from pdf_extractor.utils.llm_connection import create_openai_client, load_dotenv_if_present


def build_argument_parser() -> argparse.ArgumentParser:
    """Create the CLI argument parser."""
    load_dotenv_if_present()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", required=True, help="Path to a text-based PDF file.")
    parser.add_argument("--rules", required=True, help="Path to a JSON rule file.")
    parser.add_argument("--output", required=True, help="Path for the JSON result file.")
    parser.add_argument(
        "--llm-table-fallback",
        action="store_true",
        help="Use an optional multimodal OpenAI fallback when local table extraction fails.",
    )
    parser.add_argument(
        "--table-llm-model",
        default=os.getenv("TABLE_LLM_MODEL", "gpt-4.1-mini"),
        help="OpenAI model used by the optional multimodal table fallback.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the extraction pipeline and write JSON results."""
    arguments = build_argument_parser().parse_args(argv)
    configure_logging()
    logger = logging.getLogger(__name__)
    try:
        document = PDFParser().parse(arguments.pdf)
        logger.info(
            "Parsed PDF: pages=%d paragraphs=%d sections=%d",
            len(document.pages),
            len(document.paragraphs),
            len(document.sections),
        )
        rules = RuleLoader().load(arguments.rules)
        logger.info("Loaded rules: count=%d", len(rules))
        table_extractor = TableExtractor()
        if arguments.llm_table_fallback:
            table_extractor = TableExtractor(
                llm_assistant=MultimodalTableLLMExtractor(
                    create_openai_client(),
                    model=arguments.table_llm_model,
                )
            )
            logger.info("Enabled multimodal table fallback: model=%s", arguments.table_llm_model)
        with FTSIndexer() as indexer:
            indexer.build(document)
            report = RuleExecutor(
                indexer, table_extractor=table_extractor
            ).execute_with_diagnostics(document, rules)
    except (FileNotFoundError, OSError, RuntimeError, ValueError, json.JSONDecodeError) as error:
        logger.error("%s", error)
        return 1

    output_path = Path(arguments.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(
            {
                "file_path": document.file_path,
                "results": [result.to_dict() for result in report.results],
                "diagnostics": [
                    diagnostic.to_dict() for diagnostic in report.diagnostics
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    logger.info("Wrote output: %s", output_path)
    return 0 if report.results else 2


if __name__ == "__main__":
    raise SystemExit(main())
