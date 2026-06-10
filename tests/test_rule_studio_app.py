"""Streamlit smoke tests for the Rule Studio shell."""

from pathlib import Path

import pymupdf
from streamlit.testing.v1 import AppTest

from pdf_extractor.models import Document, Section
from rule_studio.app import apply_tree_selection


def create_pdf_bytes() -> bytes:
    document = pymupdf.open()
    for page_number in range(1, 4):
        page = document.new_page()
        page.insert_text((72, 72), f"Page {page_number}", fontsize=16)
        page.insert_text(
            (72, 110),
            f"Content for page {page_number}.",
            fontsize=11,
        )
    document.set_toc(
        [
            [1, "Chapter 1", 1],
            [2, "Revenue", 2],
            [1, "Chapter 2", 3],
        ]
    )
    content = document.tobytes()
    document.close()
    return content


def create_table_pdf_bytes() -> bytes:
    document = pymupdf.open()
    page = document.new_page(width=300, height=400)
    page.insert_text((50, 30), "Income statement", fontsize=16)
    for x in (50, 150, 250):
        page.draw_line((x, 50), (x, 110))
    for y in (50, 80, 110):
        page.draw_line((50, y), (250, y))
    page.insert_text((60, 70), "Item", fontsize=10)
    page.insert_text((160, 70), "Amount", fontsize=10)
    page.insert_text((60, 100), "Net income", fontsize=10)
    page.insert_text((160, 100), "12.5 billion", fontsize=10)
    content = document.tobytes()
    document.close()
    return content


def test_sidebar_only_contains_pdf_upload() -> None:
    app = AppTest.from_file("rule_studio/app.py").run(timeout=30)

    assert not app.exception
    assert len(app.file_uploader) == 1
    assert app.file_uploader[0].label == "PDF document"
    assert "Rule JSON" not in [uploader.label for uploader in app.file_uploader]


def test_sidebar_shows_document_structure_after_pdf_upload() -> None:
    app = AppTest.from_file("rule_studio/app.py").run(timeout=30)
    app.file_uploader[0].upload(
        "sample.pdf",
        create_pdf_bytes(),
        "application/pdf",
    ).run(timeout=30)

    assert not app.exception
    assert "Document Structure" in [subheader.value for subheader in app.subheader]
    assert len(app.sidebar.get("component_instance")) == 1
    assert len(app.main.get("component_instance")) == 2
    assert app.session_state["selected_page"] == 1


def test_sidebar_tree_selection_updates_pdf_page(monkeypatch) -> None:
    state = {
        "selected_section": "s1",
        "selected_page": 1,
        "page_input": 1,
        "report": object(),
    }
    monkeypatch.setattr("rule_studio.app.st.session_state", state)
    document = Document(
        "sample.pdf",
        sections=[
            Section("s1", "Chapter 1", 1, 1, 1, path=["Chapter 1"]),
            Section("s2", "Chapter 2", 1, 3, 3, path=["Chapter 2"]),
        ],
    )

    changed = apply_tree_selection(document, ["s1", "s2"], 1)

    assert changed
    assert state["selected_section"] == "s2"
    assert state["selected_page"] == 3
    assert state["page_input"] == 3
    assert state["report"] is None


def test_studio_uses_pdf_editor_and_test_columns() -> None:
    app = AppTest.from_file("rule_studio/app.py").run(timeout=30)
    app.file_uploader[0].upload(
        "sample.pdf",
        create_pdf_bytes(),
        "application/pdf",
    ).run(timeout=30)

    button_labels = [button.label for button in app.button]
    assert "Add rule" not in button_labels
    assert "Delete rule" not in button_labels
    assert "Run rule" in button_labels
    assert "PDF Preview" in [subheader.value for subheader in app.subheader]
    assert "Rule Editor" in [subheader.value for subheader in app.subheader]
    assert "Test Run" in [subheader.value for subheader in app.subheader]
    assert any(
        'div[data-testid="stNumberInput"] button' in markdown.value
        and "display: none" in markdown.value
        for markdown in app.markdown
    )
    assert "rule" in app.session_state.filtered_state
    assert "rules" not in app.session_state.filtered_state
    assert "**Extraction Range**" in [markdown.value for markdown in app.markdown]
    assert app.session_state["extraction_range_tabs"] == "Paragraph"
    assert "Content type" in [selectbox.label for selectbox in app.selectbox]
    assert "Scope" not in [text_input.label for text_input in app.text_input]
    assert "**Scope:** Chapter 1" in [
        markdown.value for markdown in app.markdown
    ]
    assert "**Location Scope**" not in [
        markdown.value for markdown in app.markdown
    ]
    assert "Use selected chapter as scope" not in button_labels
    assert "Table" not in [selectbox.label for selectbox in app.selectbox]


def test_paragraph_normalization_uses_type_specific_choices() -> None:
    app = AppTest.from_file("rule_studio/app.py").run(timeout=30)
    content_type = next(
        selectbox for selectbox in app.selectbox if selectbox.label == "Content type"
    )

    content_type.set_value("percentage").run(timeout=30)
    parentheses = next(
        selectbox
        for selectbox in app.selectbox
        if selectbox.label == "Parenthesized values"
    )
    assert parentheses.options == [
        "Treat parentheses as negative",
        "Treat parentheses as positive",
        "Preserve parenthesized value",
    ]
    assert "Normalization JSON" not in [
        text_area.label for text_area in app.text_area
    ]

    next(
        selectbox for selectbox in app.selectbox if selectbox.label == "Content type"
    ).set_value("date").run(timeout=30)
    automatic = next(
        selectbox
        for selectbox in app.selectbox
        if selectbox.label == "Normalization mode"
    )
    assert automatic.options == ["Automatic"]
    assert automatic.disabled


def test_table_mode_lists_current_page_tables_and_headers() -> None:
    app = AppTest.from_file("rule_studio/app.py").run(timeout=30)
    app.session_state["extraction_range_tabs"] = "Table"
    app.file_uploader[0].upload(
        "table.pdf",
        create_table_pdf_bytes(),
        "application/pdf",
    ).run(timeout=30)

    assert not app.exception
    assert "**Table Extraction**" in [markdown.value for markdown in app.markdown]
    table_select = next(
        selectbox for selectbox in app.selectbox if selectbox.label == "Table"
    )
    assert table_select.options == ["Table 1 - 2 rows × 2 columns"]
    assert not app.dataframe
    assert "**Location Scope**" not in [
        markdown.value for markdown in app.markdown
    ]
    assert "Scope" not in [text_input.label for text_input in app.text_input]
    assert "Within heading" in [text_input.label for text_input in app.text_input]
    assert "Keywords" not in [text_area.label for text_area in app.text_area]
    captions = [caption.value for caption in app.caption]
    assert "Column headers: Item, Amount" not in captions
    assert "Detected row headers: 1" not in captions
    next(
        text_input
        for text_input in app.text_input
        if text_input.label == "Within heading"
    ).set_value("Financial results")
    save_button = next(
        button for button in app.button if button.label == "Validate and save"
    )
    save_button.click().run(timeout=30)
    assert app.session_state["rule"]["extract_type"] == "table"
    assert app.session_state["rule"]["scope"] == "Income statement"
    assert app.session_state["rule"]["within_heading"] == "Financial results"
    assert app.session_state["rule"]["table_selector"] == {
        "page_number": 1,
        "table_index": 1,
    }

    output = next(
        radio for radio in app.radio if radio.label == "Extraction mode"
    )
    output.set_value("Single cell").run(timeout=30)
    selector_order = [
        selectbox.label
        for selectbox in app.selectbox
        if selectbox.label
        in {"Table", "Row header", "Column header", "Cell value type"}
    ]
    assert selector_order == [
        "Table",
        "Row header",
        "Column header",
        "Cell value type",
    ]
    selectors = {
        selectbox.label: selectbox.options
        for selectbox in app.selectbox
        if selectbox.label in {"Cell value type", "Row header", "Column header"}
    }
    assert selectors["Row header"] == ["Net income"]
    assert selectors["Column header"] == ["Item", "Amount"]
    assert selectors["Cell value type"] == [
        "text",
        "value",
        "number",
        "percentage",
        "date",
        "time",
    ]

    next(
        selectbox
        for selectbox in app.selectbox
        if selectbox.label == "Cell value type"
    ).set_value("number").run(timeout=30)
    normalization = next(
        selectbox
        for selectbox in app.selectbox
        if selectbox.label == "Parenthesized values"
    )
    assert normalization.options == [
        "Treat parentheses as negative",
        "Treat parentheses as positive",
        "Preserve parenthesized value",
    ]
    normalization.set_value("Treat parentheses as positive")
    next(
        selectbox for selectbox in app.selectbox if selectbox.label == "Row header"
    ).set_value("Net income")
    next(
        selectbox
        for selectbox in app.selectbox
        if selectbox.label == "Column header"
    ).set_value("Amount")
    next(
        button for button in app.button if button.label == "Validate and save"
    ).click().run(timeout=30)

    assert app.session_state["rule"]["extract_type"] == "number"
    assert app.session_state["rule"]["normalization"] == {
        "parentheses": "positive"
    }
    assert app.session_state["rule"]["table_selector"]["row_header"] == "Net income"
    assert app.session_state["rule"]["table_selector"]["column_header"] == "Amount"


def test_scope_automatically_tracks_current_pdf_page() -> None:
    app = AppTest.from_file("rule_studio/app.py").run(timeout=30)
    app.file_uploader[0].upload(
        "sample.pdf",
        create_pdf_bytes(),
        "application/pdf",
    ).run(timeout=30)
    app.session_state["selected_page"] = 2
    app.run(timeout=30)

    assert app.session_state["selected_page"] == 2
    assert app.session_state["selected_section"] == "s_0002"
    assert app.session_state["rule"]["scope"] == "Chapter 1 > Revenue"
    assert "**Scope:** Chapter 1 > Revenue" in [
        markdown.value for markdown in app.markdown
    ]


def test_pdf_wheel_event_changes_page() -> None:
    app = AppTest.from_file("rule_studio/app.py").run(timeout=30)
    app.file_uploader[0].upload(
        "sample.pdf",
        create_pdf_bytes(),
        "application/pdf",
    ).run(timeout=30)
    app.session_state["pdf_wheel_viewer"] = {
        "direction": "next",
        "event_id": "wheel-1",
    }
    app.run(timeout=30)

    assert app.session_state["selected_page"] == 2
    assert app.session_state["last_pdf_wheel_event"] == "wheel-1"


def test_pdf_wheel_viewer_has_distinct_interaction_cues() -> None:
    component = Path(
        "rule_studio/components/pdf_wheel_viewer/index.html"
    ).read_text(encoding="utf-8")

    assert "Scroll to change page" in component
    assert "data:image/svg+xml" in component
    assert "cursor:" in component
    assert "#viewer:hover" in component
