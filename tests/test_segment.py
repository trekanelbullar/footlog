"""区切り（設計書 §4 の5、spec B6）のテスト。"""

from ai_hackathon_team_a.segment import segment_conversation, segment_spreadsheet, segment_text_file


def test_conversation_splits_by_speaker_markers() -> None:
    text = "You: 予算はいくらですか\nChatGPT: 5万円です\nあなた: ありがとう"

    segments = segment_conversation(text)

    assert [(s.speaker, s.text) for s in segments] == [
        ("user", "予算はいくらですか"),
        ("ai", "5万円です"),
        ("user", "ありがとう"),
    ]


def test_conversation_without_markers_splits_by_blank_line_as_unknown() -> None:
    text = "最初の段落\n\n2番目の段落"

    segments = segment_conversation(text)

    assert [(s.speaker, s.text) for s in segments] == [
        ("unknown", "最初の段落"),
        ("unknown", "2番目の段落"),
    ]


def test_conversation_over_800_chars_splits_by_paragraph_then_hard_limit() -> None:
    paragraph_a = "あ" * 500
    paragraph_b = "い" * 500
    text = f"You: {paragraph_a}\n\n{paragraph_b}"

    segments = segment_conversation(text)

    assert len(segments) == 2
    assert all(len(s.text) <= 800 for s in segments)
    assert segments[0].text == paragraph_a
    assert segments[1].text == paragraph_b
    assert all(s.speaker == "user" for s in segments)


def test_conversation_hard_splits_at_800_when_no_paragraph_break() -> None:
    long_line = "あ" * 1600
    text = f"You: {long_line}"

    segments = segment_conversation(text)

    assert len(segments) == 2
    assert len(segments[0].text) == 800
    assert len(segments[1].text) == 800
    assert segments[0].text + segments[1].text == long_line


def test_text_file_splits_by_blank_line() -> None:
    text = "def a():\n    pass\n\ndef b():\n    pass"

    segments = segment_text_file(text)

    assert len(segments) == 2
    assert segments[0].speaker is None
    assert "def a" in segments[0].text
    assert "def b" in segments[1].text


def test_text_file_splits_every_20_lines_when_no_blank_line() -> None:
    lines = [f"line {i}" for i in range(45)]
    text = "\n".join(lines)

    segments = segment_text_file(text)

    assert len(segments) == 3  # 20, 20, 5
    assert segments[0].text.splitlines() == lines[0:20]
    assert segments[1].text.splitlines() == lines[20:40]
    assert segments[2].text.splitlines() == lines[40:45]


def test_spreadsheet_is_one_row_per_segment_with_locator() -> None:
    rows = [("Sheet1:1", "a,b,c"), ("Sheet1:2", "d,e,f"), ("Sheet2:1", "g,h")]

    segments = segment_spreadsheet(rows)

    assert [(s.text, s.locator, s.speaker) for s in segments] == [
        ("a,b,c", "Sheet1:1", None),
        ("d,e,f", "Sheet1:2", None),
        ("g,h", "Sheet2:1", None),
    ]


def test_spreadsheet_skips_blank_rows() -> None:
    rows = [("Sheet1:1", "a"), ("Sheet1:2", ""), ("Sheet1:3", "   ")]

    segments = segment_spreadsheet(rows)

    assert len(segments) == 1
    assert segments[0].locator == "Sheet1:1"
