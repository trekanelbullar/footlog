"""文字抽出（設計書 §4 の3）。

拡張子の許可リストと ``.xlsm`` の拒否は呼び出し側（API ルーター）の役目。ここは
``.xlsx``・``.pdf``・その他（UTF-8）の3種類の抽出だけを担当する。
"""

import csv
import io
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import openpyxl
from pypdf import PdfReader


class ExtractionError(Exception):
    """ファイルの中身を読み取れなかったことを表す（呼び出し側で400相当に変換する）。"""


@dataclass(frozen=True)
class ExtractedContent:
    """文字抽出の結果。

    ``rows`` は ``.xlsx`` のときだけ設定する（シートごとの行。伏せ字と区切りを
    行単位で行うため）。それ以外は ``None`` で、``text`` 全体を1つの単位として扱う。
    """

    text: str
    rows: list[tuple[str, str]] | None = None


def extract_text(filename: str, data: bytes) -> ExtractedContent:
    """ファイルの中身から文字を取り出す。読めない場合は :class:`ExtractionError`。"""

    suffix = Path(filename).suffix.lower()
    if suffix == ".xlsx":
        return _extract_xlsx(data)
    if suffix == ".pdf":
        return _extract_pdf(data)
    return _extract_plain_text(data)


def _extract_xlsx(data: bytes) -> ExtractedContent:
    try:
        workbook = openpyxl.load_workbook(io.BytesIO(data), data_only=True, read_only=True)
    except Exception as exc:
        raise ExtractionError("Excel ファイルを読み取れませんでした。") from exc

    rows: list[tuple[str, str]] = []
    text_lines: list[str] = []
    try:
        for sheet in workbook.worksheets:
            text_lines.append(f"# {sheet.title}")
            for row_no, values in enumerate(sheet.iter_rows(values_only=True), start=1):
                csv_line = _row_to_csv(values)
                rows.append((f"{sheet.title}:{row_no}", csv_line))
                text_lines.append(csv_line)
    finally:
        workbook.close()

    return ExtractedContent(text="\n".join(text_lines), rows=rows)


def _extract_pdf(data: bytes) -> ExtractedContent:
    try:
        reader = PdfReader(io.BytesIO(data))
        pages = [page.extract_text() or "" for page in reader.pages]
    except Exception as exc:
        raise ExtractionError("PDF を読み取れませんでした。") from exc
    return ExtractedContent(text="\n\n".join(pages))


def _extract_plain_text(data: bytes) -> ExtractedContent:
    try:
        return ExtractedContent(text=data.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise ExtractionError("UTF-8 として読み取れませんでした。") from exc


def _row_to_csv(values: Sequence[object]) -> str:
    """1行のセルの値を CSV の1行にする（改行はスペースに寄せて1行を保つ）。"""

    cleaned = ["" if v is None else str(v).replace("\r", " ").replace("\n", " ") for v in values]
    buffer = io.StringIO()
    csv.writer(buffer, lineterminator="").writerow(cleaned)
    return buffer.getvalue()
