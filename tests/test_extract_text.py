"""文字抽出（設計書 §4 の3）のテスト。"""

import io

import openpyxl
import pytest
from pypdf import PdfWriter

from ai_hackathon_team_a.extract_text import ExtractionError, extract_text


def test_plain_text_is_decoded_as_utf8() -> None:
    result = extract_text("notes.txt", "こんにちは".encode())

    assert result.text == "こんにちは"
    assert result.rows is None


def test_plain_text_raises_extraction_error_when_not_utf8() -> None:
    with pytest.raises(ExtractionError):
        extract_text("notes.txt", "こんにちは".encode("shift_jis"))


def test_xlsx_extracts_rows_with_sheet_and_row_locator() -> None:
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "Sheet1"
    sheet.append(["a", "b"])
    sheet.append(["c", "d"])
    buffer = io.BytesIO()
    workbook.save(buffer)

    result = extract_text("data.xlsx", buffer.getvalue())

    assert result.rows is not None
    assert result.rows[0] == ("Sheet1:1", "a,b")
    assert result.rows[1] == ("Sheet1:2", "c,d")


def test_xlsx_invalid_file_raises_extraction_error() -> None:
    with pytest.raises(ExtractionError):
        extract_text("data.xlsx", b"not a real xlsx file")


def test_pdf_extracts_text_per_page() -> None:
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    buffer = io.BytesIO()
    writer.write(buffer)

    result = extract_text("doc.pdf", buffer.getvalue())

    assert result.rows is None
    assert isinstance(result.text, str)


def test_pdf_invalid_file_raises_extraction_error() -> None:
    with pytest.raises(ExtractionError):
        extract_text("doc.pdf", b"not a real pdf file")
