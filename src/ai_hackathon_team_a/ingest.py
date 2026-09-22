"""取り込みのサービス層（設計書 §4、§3.1）。

W8（会話の貼り付け）・W9（ファイルのアップロード）の受け取り以降
（文字抽出 → 伏せ字 → 区切りと番号振り → 保存）を1トランザクションで行う。
拡張子・サイズ・貼り付け文字数の検証は呼び出し側（API ルーター）の役目。
"""

import hashlib
import mimetypes
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import psycopg

from ai_hackathon_team_a import extract_text, segment, storage
from ai_hackathon_team_a.api.errors import ApiError
from ai_hackathon_team_a.authz import Membership, Visibility
from ai_hackathon_team_a.extract_text import ExtractionError
from ai_hackathon_team_a.redact import redact
from ai_hackathon_team_a.segment import RawSegment
from ai_hackathon_team_a.worker_settings import WorkerSettings

_SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._-]+")
# 1ファイルの区切り数の上限。超えたら先頭から打ち切り、応答で警告する。
MAX_SEGMENTS_PER_FILE = 2000


@dataclass(frozen=True)
class IngestResult:
    """W8・W9 の応答を組み立てるための結果。"""

    source_id: UUID
    source_no: int
    version_no: int
    segment_count: int
    new_segment_count: int
    redaction_count: int
    # AD-8：W8（会話の貼り付け）だけで埋める、話者の目印の内訳。それ以外の取り込み
    # （W9・W23）では使わないので None のまま。
    speaker_counts: dict[str, int] | None = None
    # W9 だけで使う：文字を抽出できなかった（ファイル名だけを記録し、区切りは作らない）、
    # 区切りが上限を超えたので先頭から打ち切った。
    extraction_failed: bool = False
    truncated: bool = False

    @property
    def label_prefix(self) -> str:
        return f"S{self.source_no}"


def ingest_conversation(
    conn: psycopg.Connection,
    *,
    project_id: UUID,
    uploaded_by: UUID,
    text: str,
    recorded_at: datetime | None,
    visibility: Visibility,
) -> IngestResult:
    """会話の貼り付けを取り込む（W8）。常に新しいソース・version_no=1 になる。"""

    full_text, redaction_count = redact(text)

    source_no = _allocate_source_no(conn, project_id)
    source_id = uuid4()
    conn.execute(
        """
        INSERT INTO project_sources
            (id, project_id, source_no, type, uploaded_by, recorded_at, visibility, next_seq)
        VALUES (%s, %s, %s, 'conversation', %s, %s, %s, 1)
        """,
        (source_id, project_id, source_no, uploaded_by, recorded_at or _now(), visibility),
    )

    version_id = _insert_version(conn, source_id=source_id, version_no=1, full_text=full_text)
    raw_segments = segment.segment_conversation(full_text)
    inserted = _insert_segments(
        conn,
        project_id=project_id,
        source_no=source_no,
        version_id=version_id,
        base_seq=1,
        raw_segments=raw_segments,
        previous_texts=set(),
    )
    _finalize_source(
        conn, source_id=source_id, version_id=version_id, next_seq=1 + len(raw_segments)
    )

    return IngestResult(
        source_id=source_id,
        source_no=source_no,
        version_no=1,
        segment_count=len(inserted),
        new_segment_count=sum(inserted),
        redaction_count=redaction_count,
        speaker_counts=_speaker_counts(raw_segments),
    )


def _speaker_counts(raw_segments: list[RawSegment]) -> dict[str, int]:
    """AD-8：会話の貼り付けの区切りを、話者ごとに数える（user・ai・unknown）。"""

    counts = {"user": 0, "ai": 0, "unknown": 0}
    for seg in raw_segments:
        counts[seg.speaker or "unknown"] += 1
    return counts


def ingest_file(
    conn: psycopg.Connection,
    *,
    project_id: UUID,
    uploaded_by: UUID,
    filename: str,
    data: bytes,
    recorded_at: datetime | None,
    visibility: Visibility,
    settings: WorkerSettings,
    storage_transport: httpx.BaseTransport | None = None,
) -> IngestResult:
    """ファイルのアップロードを取り込む（W9）。

    同じ登録者・同じファイル名なら既存ソースの新しい版になる（§3.1、【C-3】）。
    再アップロードでは ``visibility`` の入力を無視する。
    """

    # 抽出に失敗したファイルはエラーにせず、ファイル名だけを記録する（区切りは作らないので
    # レポートには載らない）。
    try:
        extracted = extract_text.extract_text(filename, data)
        extraction_failed = False
    except ExtractionError:
        extracted = extract_text.ExtractedContent(text="")
        extraction_failed = True

    redacted_blocks: list[tuple[int, str]] | None = None
    if extracted.rows is not None:
        redacted_rows: list[tuple[str, str]] = []
        redaction_count = 0
        for locator, row_text in extracted.rows:
            clean, count = redact(row_text)
            redacted_rows.append((locator, clean))
            redaction_count += count
        full_text = "\n".join(text for _, text in redacted_rows)
    elif extracted.blocks is not None:
        redacted_rows = None
        redacted_blocks = []
        redaction_count = 0
        for number, line in extracted.blocks:
            clean, count = redact(line)
            redacted_blocks.append((number, clean))
            redaction_count += count
        full_text = "\n".join(text for _, text in redacted_blocks)
    else:
        redacted_rows = None
        full_text, redaction_count = redact(extracted.text)

    existing = conn.execute(
        """
        SELECT id, source_no, next_seq, current_version_id
        FROM project_sources
        WHERE project_id = %s AND uploaded_by = %s AND filename = %s AND type = 'file'
        FOR UPDATE
        """,
        (project_id, uploaded_by, filename),
    ).fetchone()

    resolved_recorded_at = recorded_at or _now()

    if existing is None:
        source_no = _allocate_source_no(conn, project_id)
        source_id = uuid4()
        conn.execute(
            """
            INSERT INTO project_sources
                (id, project_id, source_no, type, filename, uploaded_by,
                 recorded_at, visibility, next_seq)
            VALUES (%s, %s, %s, 'file', %s, %s, %s, %s, 1)
            """,
            (
                source_id,
                project_id,
                source_no,
                filename,
                uploaded_by,
                resolved_recorded_at,
                visibility,
            ),
        )
        base_seq = 1
        previous_texts: set[str] = set()
        version_no = 1
    else:
        source_id, source_no, base_seq, current_version_id = existing
        version_no = conn.execute(
            "SELECT COALESCE(MAX(version_no), 0) + 1 FROM source_versions WHERE source_id = %s",
            (source_id,),
        ).fetchone()[0]
        previous_texts = _previous_segment_texts(conn, current_version_id)
        # visibility の入力は無視する（既存ソースの可視性は変えない）【C-3】。
        conn.execute(
            "UPDATE project_sources SET recorded_at = %s WHERE id = %s",
            (resolved_recorded_at, source_id),
        )

    storage_path = _storage_path(project_id, source_no, version_no, filename)
    try:
        storage.upload_object(
            storage_path,
            data,
            content_type=_guess_content_type(filename),
            settings=settings,
            transport=storage_transport,
        )
    except storage.StorageError as exc:
        raise ApiError(502, "storage_error", "ファイルの保存に失敗しました。") from exc

    version_id = _insert_version(
        conn,
        source_id=source_id,
        version_no=version_no,
        full_text=full_text,
        storage_path=storage_path,
    )

    if extraction_failed:
        raw_segments = []
    elif redacted_rows is not None:
        raw_segments = segment.segment_spreadsheet(redacted_rows)
    elif redacted_blocks is not None:
        raw_segments = segment.segment_blocks(redacted_blocks)
    else:
        raw_segments = segment.segment_text_file(full_text)
    truncated = len(raw_segments) > MAX_SEGMENTS_PER_FILE
    raw_segments = raw_segments[:MAX_SEGMENTS_PER_FILE]

    inserted = _insert_segments(
        conn,
        project_id=project_id,
        source_no=source_no,
        version_id=version_id,
        base_seq=base_seq,
        raw_segments=raw_segments,
        previous_texts=previous_texts,
    )
    _finalize_source(
        conn, source_id=source_id, version_id=version_id, next_seq=base_seq + len(raw_segments)
    )

    return IngestResult(
        source_id=source_id,
        source_no=source_no,
        version_no=version_no,
        segment_count=len(inserted),
        new_segment_count=sum(inserted),
        redaction_count=redaction_count,
        extraction_failed=extraction_failed,
        truncated=truncated,
    )


def ingest_answer(
    conn: psycopg.Connection,
    *,
    project_id: UUID,
    uploaded_by: UUID,
    text: str,
    visibility: Visibility,
) -> IngestResult:
    """質問への回答を取り込む（W23、設計書 §5.4、【C-2】）。

    ``type = 'answer'`` の新しいソースとして§4の取り込みを通す（伏せ字も通る）。
    話者は ``user`` 固定。可視性は呼び出し側（回答した時点の、元の出来事の実効の
    可視性）が決めて渡す。会話の話者の目印は解釈せず、回答の全文を1つの区切りに
    する。
    """

    full_text, redaction_count = redact(text)

    source_no = _allocate_source_no(conn, project_id)
    source_id = uuid4()
    conn.execute(
        """
        INSERT INTO project_sources
            (id, project_id, source_no, type, uploaded_by, recorded_at, visibility, next_seq)
        VALUES (%s, %s, %s, 'answer', %s, %s, %s, 1)
        """,
        (source_id, project_id, source_no, uploaded_by, _now(), visibility),
    )

    version_id = _insert_version(conn, source_id=source_id, version_no=1, full_text=full_text)
    raw_segments = [RawSegment(text=full_text, speaker="user")] if full_text.strip() else []
    inserted = _insert_segments(
        conn,
        project_id=project_id,
        source_no=source_no,
        version_id=version_id,
        base_seq=1,
        raw_segments=raw_segments,
        previous_texts=set(),
    )
    _finalize_source(
        conn, source_id=source_id, version_id=version_id, next_seq=1 + len(raw_segments)
    )

    return IngestResult(
        source_id=source_id,
        source_no=source_no,
        version_no=1,
        segment_count=len(inserted),
        new_segment_count=sum(inserted),
        redaction_count=redaction_count,
    )


def list_sources(
    conn: psycopg.Connection, *, project_id: UUID, membership: Membership
) -> list[dict]:
    """W10：所属者の閲覧範囲（§1.3）で絞ったソース一覧。"""

    visibility_filter = (
        "" if membership.role == "manager" else "AND (ps.visibility = 'all' OR ps.uploaded_by = %s)"
    )
    params: tuple[object, ...] = (project_id,)
    if membership.role != "manager":
        params = (project_id, membership.user_id)

    rows = conn.execute(
        f"""
        SELECT ps.id, ps.source_no, ps.type, ps.filename, ps.uploaded_by, ps.recorded_at,
               ps.visibility, ps.is_excluded, ps.exclude_reason, sv.version_no
        FROM project_sources ps
        LEFT JOIN source_versions sv ON sv.id = ps.current_version_id
        WHERE ps.project_id = %s {visibility_filter}
        ORDER BY ps.source_no
        """,
        params,
    ).fetchall()

    return [
        {
            "source_id": row[0],
            "source_no": row[1],
            "type": row[2],
            "filename": row[3],
            "uploaded_by": row[4],
            "recorded_at": row[5],
            "visibility": row[6],
            "is_excluded": row[7],
            "exclude_reason": row[8],
            "current_version_no": row[9],
        }
        for row in rows
    ]


def fetch_current_storage_path(conn: psycopg.Connection, *, version_id: UUID | None) -> str | None:
    """W13：現在の版の Storage パス（ファイル型でない、または無ければ None）。"""

    if version_id is None:
        return None
    row = conn.execute(
        "SELECT storage_path FROM source_versions WHERE id = %s", (version_id,)
    ).fetchone()
    return row[0] if row else None


def _now() -> datetime:
    return datetime.now(UTC)


def _allocate_source_no(conn: psycopg.Connection, project_id: UUID) -> int:
    """projects 行をロックして source_no を1つ払い出す（§4 の6）。"""

    row = conn.execute(
        "SELECT next_source_no FROM projects WHERE id = %s FOR UPDATE", (project_id,)
    ).fetchone()
    if row is None:
        raise ApiError(404, "not_found", "プロジェクトが見つかりません。")
    source_no: int = row[0]
    conn.execute(
        "UPDATE projects SET next_source_no = next_source_no + 1 WHERE id = %s", (project_id,)
    )
    return source_no


def _insert_version(
    conn: psycopg.Connection,
    *,
    source_id: UUID,
    version_no: int,
    full_text: str,
    storage_path: str | None = None,
) -> UUID:
    version_id = uuid4()
    content_hash = hashlib.sha256(full_text.encode("utf-8")).hexdigest()
    conn.execute(
        """
        INSERT INTO source_versions
            (id, source_id, version_no, content_hash, extracted_text, storage_path)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (version_id, source_id, version_no, content_hash, full_text, storage_path),
    )
    return version_id


def _previous_segment_texts(conn: psycopg.Connection, version_id: UUID | None) -> set[str]:
    """前の版（直前の current_version_id）の区切りの原文の集合（§3.1）。"""

    if version_id is None:
        return set()
    rows = conn.execute(
        "SELECT text FROM source_segments WHERE source_version_id = %s", (version_id,)
    ).fetchall()
    return {row[0] for row in rows}


def _insert_segments(
    conn: psycopg.Connection,
    *,
    project_id: UUID,
    source_no: int,
    version_id: UUID,
    base_seq: int,
    raw_segments: list[RawSegment],
    previous_texts: set[str],
) -> list[bool]:
    """区切りを保存し、区切りごとの ``is_new`` の一覧を返す（§3.1）。

    続きの ``seq`` を振り、前の版に同じ文字列の区切りが無いものだけ
    ``is_new=true``・``consumed=false`` にする（あるものは ``consumed=true``）。
    """

    is_new_flags: list[bool] = []
    for i, raw in enumerate(raw_segments):
        seq = base_seq + i
        label = f"S{source_no}-{seq}"
        is_new = raw.text not in previous_texts
        conn.execute(
            """
            INSERT INTO source_segments
                (project_id, source_version_id, label, seq, speaker,
                 text, locator, is_new, consumed)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                project_id,
                version_id,
                label,
                seq,
                raw.speaker,
                raw.text,
                raw.locator,
                is_new,
                not is_new,
            ),
        )
        is_new_flags.append(is_new)
    return is_new_flags


def _finalize_source(
    conn: psycopg.Connection, *, source_id: UUID, version_id: UUID, next_seq: int
) -> None:
    conn.execute(
        "UPDATE project_sources SET current_version_id = %s, next_seq = %s, updated_at = now() "
        "WHERE id = %s",
        (version_id, next_seq, source_id),
    )


def _storage_path(project_id: UUID, source_no: int, version_no: int, filename: str) -> str:
    return f"projects/{project_id}/sources/{source_no}/v{version_no}/{_safe_filename(filename)}"


def _safe_filename(filename: str) -> str:
    base = Path(filename).name
    return _SAFE_FILENAME_RE.sub("_", base) or "file"


_OFFICE_CONTENT_TYPES = {
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


def _guess_content_type(filename: str) -> str:
    office = _OFFICE_CONTENT_TYPES.get(Path(filename).suffix.lower())
    return office or mimetypes.guess_type(filename)[0] or "application/octet-stream"
