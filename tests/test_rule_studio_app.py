"""Streamlit smoke tests for the Rule Studio shell."""

import pymupdf
from streamlit.testing.v1 import AppTest


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
    assert len(app.main.get("component_instance")) == 0
    assert app.session_state["selected_page"] == 1


def test_studio_edits_one_rule_in_two_column_workspace() -> None:
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
    assert "rule" in app.session_state.filtered_state
    assert "rules" not in app.session_state.filtered_state
    assert "Extraction range" in [radio.label for radio in app.radio]
    assert "Content type" in [selectbox.label for selectbox in app.selectbox]
    assert "Scope" in [text_input.label for text_input in app.text_input]
    assert "Table" not in [selectbox.label for selectbox in app.selectbox]


def test_table_mode_lists_current_page_tables_and_headers() -> None:
    app = AppTest.from_file("rule_studio/app.py").run(timeout=30)
    app.session_state["data_scope"] = "Table"
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
    assert table_select.options == ["Table 1 · 2 rows × 2 columns"]
    assert not app.dataframe
    assert "**Location Scope**" in [markdown.value for markdown in app.markdown]
    assert "Scope" in [text_input.label for text_input in app.text_input]
    assert "Within heading" in [text_input.label for text_input in app.text_input]
    assert "Keywords" not in [text_area.label for text_area in app.text_area]
    captions = [caption.value for caption in app.caption]
    assert "Column headers: Item, Amount" in captions
    assert "Detected row headers: 1" in captions
    next(
        text_input for text_input in app.text_input if text_input.label == "Scope"
    ).set_value("Income statement")
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
