"""Visual rule authoring and testing workspace for PDF Extractor."""

from __future__ import annotations

import base64
import hashlib
import sys
from pathlib import Path
from typing import Any

import streamlit as st
import streamlit.components.v1 as components
import streamlit_antd_components as sac

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

PDF_WHEEL_VIEWER = components.declare_component(
    "pdf_wheel_viewer",
    path=str(ROOT_DIR / "rule_studio" / "components" / "pdf_wheel_viewer"),
)

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
    result_table_rows,
    resolve_tree_selection,
    rule_to_payload,
    rules_json,
    scope_for_page,
    section_for_page,
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
        "page_input": 1,
        "selected_section": None,
        "data_scope": "Paragraph",
        "paragraph_extract_type": "text",
        "table_output": "Whole table",
        "selected_table_index": 1,
        "last_pdf_wheel_event": None,
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def render_normalization_controls(
    extract_type: str,
    payload: dict[str, Any],
    *,
    key_prefix: str,
) -> dict[str, Any] | None:
    """Render the supported normalization choices for one extraction type."""
    if extract_type not in {"number", "percentage", "date", "time"}:
        return None
    with st.expander("Normalization"):
        if extract_type in {"date", "time"}:
            st.selectbox(
                "Normalization mode",
                ["Automatic"],
                disabled=True,
                key=f"{key_prefix}_automatic_normalization",
                help="Date and time values are normalized automatically.",
            )
            return None

        modes = {
            "Treat parentheses as negative": "negative",
            "Treat parentheses as positive": "positive",
            "Preserve parenthesized value": "preserve",
        }
        current_mode = (payload.get("normalization") or {}).get(
            "parentheses",
            "negative",
        )
        current_label = next(
            (
                label
                for label, value in modes.items()
                if value == current_mode
            ),
            "Treat parentheses as negative",
        )
        selected_label = st.selectbox(
            "Parenthesized values",
            list(modes),
            index=list(modes).index(current_label),
            key=f"{key_prefix}_parentheses_normalization",
        )
        return {"parentheses": modes[selected_label]}


def validate_current_rule() -> ExtractionRule:
    """Validate the only rule edited by Rule Studio."""
    return validate_rule_payload(st.session_state.rule)


def apply_tree_selection(
    document: Document,
    section_ids: list[str],
    selection: int | list[int] | None,
) -> bool:
    """Navigate to a TOC item selected in the sidebar."""
    resolved_selection = resolve_tree_selection(
        document,
        section_ids,
        selection,
    )
    if resolved_selection is None:
        return False
    section_id, start_page = resolved_selection
    if section_id == st.session_state["selected_section"]:
        return False
    st.session_state["selected_section"] = section_id
    st.session_state["selected_page"] = start_page
    st.session_state["page_input"] = start_page
    st.session_state["report"] = None
    return True


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
        active_section = section_for_page(
            document,
            st.session_state.selected_page,
        )
        st.session_state.selected_section = (
            active_section.id if active_section is not None else None
        )
        tree_items, section_ids = document_tree(document)
        with st.sidebar:
            st.subheader("Document Structure")
            if tree_items:
                selected_index = (
                    section_ids.index(st.session_state.selected_section)
                    if st.session_state.selected_section in section_ids
                    else None
                )
                tree_key = (
                    f"document_tree_{st.session_state.loaded_pdf_digest}_"
                    f"{st.session_state.selected_section or 'none'}"
                )
                selection = sac.tree(
                    items=tree_items,
                    index=selected_index,
                    open_all=True,
                    checkbox=False,
                    show_line=True,
                    return_index=True,
                    size="sm",
                    key=tree_key,
                )
                if apply_tree_selection(document, section_ids, selection):
                    st.rerun()
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
    scope = (
        scope_for_page(document, st.session_state.selected_page)
        if document is not None
        else payload.get("scope")
    )
    st.markdown(f"**Scope:** {scope or 'No TOC section for this page'}")
    if payload.get("scope") != scope:
        st.session_state.rule["scope"] = scope
        st.session_state.report = None
    within_heading = st.text_input(
        "Within heading",
        value=payload.get("within_heading") or "",
    )

    st.markdown("**Extraction Range**")
    data_scope = sac.tabs(
        ["Paragraph", "Table"],
        index=0 if st.session_state.data_scope == "Paragraph" else 1,
        variant="outline",
        use_container_width=True,
        key="extraction_range_tabs",
    )
    st.session_state.data_scope = data_scope

    keywords_text = ""
    table_selector = None
    normalization = None
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
        normalization = render_normalization_controls(
            extract_type,
            payload,
            key_prefix="paragraph",
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
            if table_output == "Whole table":
                table_selector = {
                    "page_number": st.session_state.selected_page,
                    "table_index": selected_table.table_index,
                }
                extract_type = "table"
            else:
                cell_types = ["text", "value", "number", "percentage", "date", "time"]
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
                extract_type = st.selectbox("Cell value type", cell_types)
                normalization = render_normalization_controls(
                    extract_type,
                    payload,
                    key_prefix="cell",
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
                "normalization": normalization,
                "table_strategy": table_strategy,
                "llm_input": llm_input,
            }
            validated = validate_rule_payload(updated)
        except (TypeError, ValueError) as error:
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

    st.markdown(
        """
        <style>
        div[data-testid="stNumberInput"] button {
            display: none;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
    navigation = st.columns([1, 1.5, 1], vertical_alignment="center")
    with navigation[0]:
        st.button(
            "Previous page",
            disabled=st.session_state.selected_page <= 1,
            use_container_width=True,
            on_click=change_page,
            args=(-1,),
        )
    with navigation[1]:
        st.session_state.page_input = st.session_state.selected_page

        def use_page_input() -> None:
            st.session_state.selected_page = int(st.session_state.page_input)

        page_number = st.number_input(
            "Page",
            min_value=1,
            max_value=len(document.pages),
            step=1,
            key="page_input",
            label_visibility="collapsed",
            on_change=use_page_input,
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
    wheel_event = PDF_WHEEL_VIEWER(
        image_base64=base64.b64encode(image).decode("ascii"),
        page_number=int(page_number),
        page_count=len(document.pages),
        key="pdf_wheel_viewer",
        default=None,
    )
    if (
        isinstance(wheel_event, dict)
        and wheel_event.get("event_id")
        and wheel_event["event_id"] != st.session_state.last_pdf_wheel_event
    ):
        st.session_state.last_pdf_wheel_event = wheel_event["event_id"]
        direction = wheel_event.get("direction")
        if direction == "previous":
            change_page(-1)
        elif direction == "next":
            change_page(1)
        st.rerun()
    st.caption(
        "Scroll over the preview to change pages. Orange boxes show selected "
        "tables and extraction results."
    )


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
            result_table_rows(report),
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


def render_execution_placeholder() -> None:
    """Render the test column before a PDF is available."""
    st.subheader("Test Run")
    st.info("Upload a PDF before running the rule.")


def main() -> None:
    """Run the Streamlit application."""
    st.set_page_config(page_title="PDF Rule Studio", layout="wide")
    initialize_state()
    st.title("PDF Rule Studio")
    st.caption("Configure, validate, preview, and test PDF extraction rules.")
    document = render_sidebar()

    if document is None:
        pdf_column, editor_column, test_column = st.columns(
            [1.3, 1, 1],
            gap="large",
        )
        with pdf_column:
            st.info("Upload a text-based PDF to preview pages and configure a rule.")
        with editor_column:
            render_rule_editor(None)
        with test_column:
            render_execution_placeholder()
        return

    st.caption(
        f"{len(document.pages)} pages · {len(document.sections)} sections · "
        f"{len(document.paragraphs)} paragraphs"
    )
    pdf_column, editor_column, test_column = st.columns(
        [1.3, 1, 1],
        gap="large",
    )
    with editor_column:
        editor_highlights = render_rule_editor(document)
    with test_column:
        render_execution(document)
    with pdf_column:
        render_pdf_viewer(document, editor_highlights)


if __name__ == "__main__":
    main()
