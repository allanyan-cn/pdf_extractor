"""Visual rule authoring and testing workspace for PDF Extractor."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import streamlit as st
import streamlit_antd_components as sac

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from pdf_extractor.models import Document, ExecutionReport
from pdf_extractor.rules.rule_schema import ExtractionRule
from rule_studio.services import (
    StudioTable,
    document_tree,
    execute_rules,
    load_page_tables,
    new_rule_payload,
    parse_uploaded_pdf,
    render_page_png,
    report_json,
    resolve_tree_selection,
    rule_to_payload,
    rules_json,
    section_label,
    validate_rule_payload,
)


@st.cache_resource(show_spinner="Parsing PDF...")
def cached_parse_pdf(content: bytes, filename: str) -> Document:
    """Parse uploads once per unique file content."""
    return parse_uploaded_pdf(content, filename)


@st.cache_data(show_spinner=False)
def cached_page_image(
    file_path: str,
    page_number: int,
    highlights: tuple[tuple[float, float, float, float], ...],
) -> bytes:
    """Render a page image for a stable highlight set."""
    boxes = [
        {"x0": item[0], "y0": item[1], "x1": item[2], "y1": item[3]}
        for item in highlights
    ]
    return render_page_png(file_path, page_number, boxes)


@st.cache_data(show_spinner="Detecting tables...")
def cached_page_tables(file_path: str, page_number: int) -> list[StudioTable]:
    """Detect tables once per PDF page."""
    return load_page_tables(file_path, page_number)


def initialize_state() -> None:
    """Initialize mutable editor state."""
    defaults: dict[str, Any] = {
        "rule": new_rule_payload(),
        "rule_revision": 0,
        "report": None,
        "loaded_pdf_digest": None,
        "selected_page": 1,
        "selected_section": None,
        "data_scope": "Paragraph",
        "paragraph_extract_type": "text",
        "table_output": "Whole table",
        "selected_table_index": 1,
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def parse_optional_json(value: str, field_name: str) -> dict[str, Any] | None:
    """Parse an optional JSON object from a textarea."""
    if not value.strip():
        return None
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise ValueError(f"{field_name} must be a JSON object.")
    return parsed


def validate_current_rule() -> ExtractionRule:
    """Validate the only rule edited by Rule Studio."""
    return validate_rule_payload(st.session_state.rule)


def render_sidebar() -> Document | None:
    """Render the PDF upload and the parsed document outline."""
    st.sidebar.header("PDF")
    uploaded_pdf = st.sidebar.file_uploader("PDF document", type=["pdf"])

    document = None
    if uploaded_pdf is not None:
        pdf_content = uploaded_pdf.getvalue()
        pdf_digest = hashlib.sha256(pdf_content).hexdigest()
        if pdf_digest != st.session_state.loaded_pdf_digest:
            st.session_state.loaded_pdf_digest = pdf_digest
            st.session_state.report = None
            st.session_state.selected_page = 1
            st.session_state.selected_section = None
            st.session_state.selected_table_index = 1
        try:
            document = cached_parse_pdf(pdf_content, uploaded_pdf.name)
        except (OSError, RuntimeError, ValueError) as error:
            st.sidebar.error(f"PDF parse error: {error}")

    if document is not None:
        tree_items, section_ids = document_tree(document)
        with st.sidebar:
            st.subheader("Document Structure")
            if tree_items:
                selected_index = (
                    section_ids.index(st.session_state.selected_section)
                    if st.session_state.selected_section in section_ids
                    else None
                )
                selection = sac.tree(
                    items=tree_items,
                    index=selected_index,
                    open_all=True,
                    checkbox=False,
                    show_line=True,
                    return_index=True,
                    size="sm",
                    key=f"document_tree_{st.session_state.loaded_pdf_digest}",
                )
                resolved_selection = resolve_tree_selection(
                    document,
                    section_ids,
                    selection,
                )
                if resolved_selection is not None:
                    section_id, start_page = resolved_selection
                    if section_id != st.session_state.selected_section:
                        st.session_state.selected_section = section_id
                        st.session_state.selected_page = start_page
            else:
                st.info("No document sections were detected.")
            st.caption(
                f"{len(document.pages)} pages · {len(document.sections)} sections"
            )
    return document


def render_rule_editor(document: Document | None) -> list[dict[str, float]]:
    """Render the single rule editor and return selected table highlights."""
    st.subheader("Rule Editor")
    payload = st.session_state.rule
    highlights: list[dict[str, float]] = []

    st.markdown("**Basic Information**")
    rule_id = st.text_input("Rule id", value=payload.get("id", ""))
    name = st.text_input("Name", value=payload.get("name", ""))
    target = st.text_input("Target", value=payload.get("target", ""))
    data_scope = st.radio(
        "Extraction range",
        ["Paragraph", "Table"],
        horizontal=True,
        key="data_scope",
    )

    st.markdown("**Location Scope**")
    scope_help = "Use a full section path with ' > ' when titles repeat."
    if document and document.sections:
        scope_help += " Select a chapter in the sidebar to use its full path."
    scope = st.text_input(
        "Scope",
        value=payload.get("scope") or "",
        help=scope_help,
    )
    if document and st.session_state.selected_section:
        selected_scope = section_label(document, st.session_state.selected_section)
        if st.button("Use selected chapter as scope", use_container_width=True):
            st.session_state.rule["scope"] = selected_scope
            st.session_state.rule_revision += 1
            st.rerun()
    within_heading = st.text_input(
        "Within heading",
        value=payload.get("within_heading") or "",
    )

    keywords_text = ""
    table_selector = None
    normalization_text = ""
    table_strategy = "auto"
    llm_input = "page_image"

    if data_scope == "Paragraph":
        st.markdown("**Paragraph Extraction**")
        paragraph_types = ["text", "value", "number", "percentage", "date", "time"]
        extract_type = st.selectbox(
            "Content type",
            paragraph_types,
            key="paragraph_extract_type",
        )
        keywords_text = st.text_area(
            "Keywords",
            value="\n".join(payload.get("keywords", [])),
            help="One keyword per line.",
        )
        if extract_type in {"number", "percentage", "date", "time"}:
            with st.expander("Normalization"):
                normalization_text = st.text_area(
                    "Normalization JSON",
                    value=json.dumps(
                        payload.get("normalization"),
                        ensure_ascii=False,
                        indent=2,
                    )
                    if payload.get("normalization")
                    else "",
                )
    else:
        st.markdown("**Table Extraction**")
        table_output = st.radio(
            "Extraction mode",
            ["Whole table", "Single cell"],
            horizontal=True,
            key="table_output",
        )
        extract_type = "table"
        with st.expander("Table strategy"):
            table_strategy = st.selectbox(
                "Table strategy",
                ["auto", "local", "llm"],
                index=["auto", "local", "llm"].index(
                    payload.get("table_strategy", "auto")
                ),
            )
            llm_input = st.selectbox(
                "LLM input",
                ["page_image", "text"],
                index=["page_image", "text"].index(
                    payload.get("llm_input", "page_image")
                ),
            )

    if data_scope == "Table" and document is not None:
        tables = cached_page_tables(document.file_path, st.session_state.selected_page)
        if not tables:
            st.warning(f"No tables were detected on page {st.session_state.selected_page}.")
        else:
            table_indexes = [table.table_index for table in tables]
            if st.session_state.selected_table_index not in table_indexes:
                st.session_state.selected_table_index = table_indexes[0]
            selected_table_index = st.selectbox(
                "Table",
                table_indexes,
                format_func=lambda index: next(
                    table.label for table in tables if table.table_index == index
                ),
                key="selected_table_index",
            )
            selected_table = next(
                table for table in tables if table.table_index == selected_table_index
            )
            highlights.append(as_bbox_dict(selected_table.bbox))
            st.caption(
                f"Page {st.session_state.selected_page} · {selected_table.label}"
            )
            if selected_table.column_headers:
                st.caption(
                    "Column headers: " + ", ".join(selected_table.column_headers)
                )
            if selected_table.row_headers:
                st.caption(
                    f"Detected row headers: {len(selected_table.row_headers)}"
                )
            if table_output == "Whole table":
                table_selector = {
                    "page_number": st.session_state.selected_page,
                    "table_index": selected_table.table_index,
                }
                extract_type = "table"
            else:
                cell_types = ["text", "value", "number", "percentage", "date", "time"]
                extract_type = st.selectbox("Cell value type", cell_types)
                row_header = st.selectbox(
                    "Row header",
                    selected_table.row_headers,
                    index=None,
                    placeholder="Select a row header",
                )
                column_header = st.selectbox(
                    "Column header",
                    selected_table.column_headers,
                    index=None,
                    placeholder="Select a column header",
                )
                if row_header and column_header:
                    table_selector = {
                        "page_number": st.session_state.selected_page,
                        "table_index": selected_table.table_index,
                        "row_header": row_header,
                        "column_header": column_header,
                    }
                else:
                    st.info("Select both a row header and a column header.")

    if st.button("Validate and save", type="primary", use_container_width=True):
        try:
            if data_scope == "Table" and table_selector is None:
                raise ValueError("Select a table, or select both row and column headers.")
            updated: dict[str, Any] = {
                "id": rule_id.strip(),
                "name": name.strip(),
                "scope": scope.strip() or None,
                "keywords": [
                    keyword.strip()
                    for keyword in keywords_text.splitlines()
                    if keyword.strip()
                ],
                "extract_type": extract_type,
                "target": target.strip(),
                "priority": 0,
                "within_heading": within_heading.strip() or None,
                "table_selector": table_selector,
                "normalization": parse_optional_json(
                    normalization_text, "normalization"
                ),
                "table_strategy": table_strategy,
                "llm_input": llm_input,
            }
            validated = validate_rule_payload(updated)
        except (json.JSONDecodeError, TypeError, ValueError) as error:
            st.error(str(error))
        else:
            st.session_state.rule = rule_to_payload(validated)
            st.session_state.report = None
            st.success("Rule is valid and saved.")

    try:
        valid_rule = validate_current_rule()
    except (TypeError, ValueError) as error:
        st.warning(f"Rule is not ready to export: {error}")
    else:
        st.download_button(
            "Download rules.json",
            data=rules_json([valid_rule]),
            file_name="rules.json",
            mime="application/json",
            use_container_width=True,
        )
    return highlights


def result_highlights(report: ExecutionReport | None, page_number: int) -> list[dict[str, float]]:
    """Collect result boxes for one page."""
    if report is None:
        return []
    highlights = []
    for result in report.results:
        if result.page_number == page_number:
            highlights.append(as_bbox_dict(result.bbox))
        if result.page_numbers and result.bboxes:
            highlights.extend(
                as_bbox_dict(bbox)
                for result_page, bbox in zip(result.page_numbers, result.bboxes)
                if result_page == page_number
            )
    return highlights


def as_bbox_dict(bbox: Any) -> dict[str, float]:
    """Normalize a bbox dataclass for rendering."""
    return {
        "x0": float(bbox.x0),
        "y0": float(bbox.y0),
        "x1": float(bbox.x1),
        "y1": float(bbox.y1),
    }


def render_pdf_viewer(
    document: Document,
    editor_highlights: list[dict[str, float]],
) -> None:
    """Render the current PDF page with paragraph and result highlights."""
    st.subheader("PDF Preview")
    st.session_state.selected_page = min(
        max(int(st.session_state.selected_page), 1),
        len(document.pages),
    )

    def change_page(delta: int) -> None:
        st.session_state.selected_page = min(
            max(st.session_state.selected_page + delta, 1),
            len(document.pages),
        )

    navigation = st.columns([1, 1.5, 1])
    with navigation[0]:
        st.button(
            "Previous page",
            disabled=st.session_state.selected_page <= 1,
            use_container_width=True,
            on_click=change_page,
            args=(-1,),
        )
    with navigation[1]:
        page_number = st.number_input(
            "Page",
            min_value=1,
            max_value=len(document.pages),
            step=1,
            key="selected_page",
            label_visibility="collapsed",
        )
    with navigation[2]:
        st.button(
            "Next page",
            disabled=st.session_state.selected_page >= len(document.pages),
            use_container_width=True,
            on_click=change_page,
            args=(1,),
        )
    st.caption(f"Page {int(page_number)} of {len(document.pages)}")
    highlights = editor_highlights + result_highlights(
        st.session_state.report,
        int(page_number),
    )
    highlight_key = tuple(
        (box["x0"], box["y0"], box["x1"], box["y1"])
        for box in highlights
    )
    image = cached_page_image(document.file_path, int(page_number), highlight_key)
    st.image(image, use_container_width=True)
    st.caption("Orange boxes show the selected paragraph and extraction results.")


def render_execution(document: Document) -> None:
    """Run the single rule and render diagnostics."""
    st.subheader("Test Run")
    run_rule = st.button(
        "Run rule",
        use_container_width=True,
        type="primary",
    )
    if run_rule:
        try:
            rule = validate_current_rule()
            with st.spinner("Executing extraction rules..."):
                st.session_state.report = execute_rules(document, [rule])
        except (OSError, RuntimeError, TypeError, ValueError) as error:
            st.error(f"Execution failed: {error}")

    report: ExecutionReport | None = st.session_state.report
    if report is None:
        st.info("Run a rule to inspect results and diagnostics.")
        return

    st.metric("Results", len(report.results))
    for diagnostic in report.diagnostics:
        with st.expander(
            f"{diagnostic.rule_id}: {diagnostic.status}",
            expanded=diagnostic.status != "success",
        ):
            st.write(diagnostic.message)
            st.json(diagnostic.to_dict())
    if report.results:
        st.dataframe(
            [
                {
                    "rule_id": result.rule_id,
                    "value": result.value,
                    "page": result.page_number,
                    "confidence": result.confidence,
                    "source": result.source_text,
                }
                for result in report.results
            ],
            use_container_width=True,
            hide_index=True,
        )
    st.download_button(
        "Download extraction result",
        data=report_json(document, report),
        file_name="extraction-output.json",
        mime="application/json",
        use_container_width=True,
    )


def main() -> None:
    """Run the Streamlit application."""
    st.set_page_config(page_title="PDF Rule Studio", layout="wide")
    initialize_state()
    st.title("PDF Rule Studio")
    st.caption("Configure, validate, preview, and test PDF extraction rules.")
    document = render_sidebar()

    if document is None:
        left_column, right_column = st.columns([1.35, 1])
        with left_column:
            st.info("Upload a text-based PDF to preview pages and configure a rule.")
        with right_column:
            render_rule_editor(None)
        return

    st.caption(
        f"{len(document.pages)} pages · {len(document.sections)} sections · "
        f"{len(document.paragraphs)} paragraphs"
    )
    pdf_column, editor_column = st.columns([1.35, 1])
    with editor_column:
        editor_highlights = render_rule_editor(document)
        render_execution(document)
    with pdf_column:
        render_pdf_viewer(document, editor_highlights)


if __name__ == "__main__":
    main()
