"""Office ファイル（docx・pptx・xlsx）の文字抽出と区切り。

fixture は tests/fixtures/make_office_fixtures.py で作る。
"""

from pathlib import Path

import pytest

from ai_hackathon_team_a import extract_text, segment

FIXTURES = Path(__file__).parent / "fixtures"


def _segments(name: str) -> list[tuple[str | None, str]]:
    content = extract_text.extract_text(name, (FIXTURES / name).read_bytes())
    if content.blocks is not None:
        raw = segment.segment_blocks(content.blocks)
    else:
        assert content.rows is not None
        raw = segment.segment_spreadsheet(content.rows)
    return [(s.locator, s.text) for s in raw]


def test_docx_keeps_document_order_headings_tables_and_paragraph_locators() -> None:
    segments = _segments("sample.docx")

    assert segments == [
        (
            "段落1-3",
            "## 配信方式の検討\n通知メールの送信には外部サービスを使うことに決めた。\n"
            "理由は、自前でメールサーバーを持つと運用の手間が大きいため。",
        ),
        ("段落5-6", "案 | 費用 | 判断\n自前サーバー | 高い | 却下"),
        ("段落7", "次は文面の確認を行う。"),
    ]
    # ヘッダー・フッターは対象外
    assert all("ヘッダー" not in text and "フッター" not in text for _, text in segments)


def test_pptx_one_segment_per_slide_notes_separate_group_and_table_text() -> None:
    assert _segments("sample.pptx") == [
        ("スライド1", "## 第1回の定例\nデモは5分で行うことに決めた"),
        ("スライド1のノート", "時間が足りなければ質疑を短くする"),
        ("スライド2", "## 残った論点\nグループの中の文字：資料はA4一枚\n論点 | 状態\n文面 | 未定"),
    ]


def test_xlsx_one_row_per_segment_with_sheet_and_row_number_and_no_empty_rows() -> None:
    assert _segments("sample.xlsx") == [
        ("進捗:1", "項目,状態,メモ"),
        ("進捗:2", "デプロイ,完了,東京リージョン"),
        ("進捗:4", "通知,作業中,文面は未定"),
        ("決定:1", "配信は外部サービス,理由：運用の手間"),
    ]


def test_docx_splits_every_20_lines_without_blank_lines() -> None:
    blocks = [(n, f"行{n}") for n in range(1, 46)]

    segments = segment.segment_blocks(blocks)

    assert [s.locator for s in segments] == ["段落1-20", "段落21-40", "段落41-45"]


@pytest.mark.parametrize("name", ["broken.docx", "broken.pptx", "broken.xlsx"])
def test_broken_office_files_raise_extraction_error(name: str) -> None:
    with pytest.raises(extract_text.ExtractionError):
        extract_text.extract_text(name, b"not a zip file")
