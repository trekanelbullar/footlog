"""ソースの登録・取り込み・可視性・除外の API（W8〜W13、設計書 §2.2・§4）。"""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal
from uuid import UUID

import httpx
import psycopg
from fastapi import APIRouter, Depends, File, Form, UploadFile
from pydantic import BaseModel, Field

from ai_hackathon_team_a import authz, ingest, storage
from ai_hackathon_team_a.api.deps import (
    get_current_user,
    get_db_connection,
    get_storage_transport,
    require_worker_secret,
)
from ai_hackathon_team_a.api.errors import ApiError
from ai_hackathon_team_a.auth import AuthenticatedUser
from ai_hackathon_team_a.authz import Membership, Visibility
from ai_hackathon_team_a.worker_settings import WorkerSettings, get_worker_settings

router = APIRouter(dependencies=[Depends(require_worker_secret)])

# 拡張子の許可リスト（設計書 §4 の1）。.xlsm はここに無いので拒否される。
_ALLOWED_EXTENSIONS = {
    ".txt",
    ".md",
    ".csv",
    ".py",
    ".ts",
    ".js",
    ".json",
    ".xlsx",
    ".docx",
    ".pptx",
    ".pdf",
}
# 古い形式とマクロ付きの形式は受け付けず、新しい形式で保存し直すよう案内する。
_RESAVE_EXTENSIONS = {".xls", ".doc", ".ppt", ".xlsm"}
_MAX_FILE_BYTES = 10 * 1024 * 1024
_MAX_PASTE_CHARS = 200_000

ExcludeReason = Literal["private", "confidential", "irrelevant", "other"]


class ConversationSourceRequest(BaseModel):
    text: str = Field(min_length=1)
    recorded_at: datetime | None = None
    visibility: Visibility


class VisibilityChangeRequest(BaseModel):
    visibility: Visibility
    reason: ExcludeReason


class ExclusionChangeRequest(BaseModel):
    is_excluded: bool
    reason: ExcludeReason


@router.post("/internal/projects/{pid}/sources/conversation")
def create_conversation_source(
    pid: UUID,
    payload: ConversationSourceRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    conn: psycopg.Connection = Depends(get_db_connection),
) -> dict:
    """W8：会話の貼り付け。取り込みをこのリクエストの中で同期的に終える（U6）。"""

    _require_membership(conn, project_id=pid, user_id=user.user_id)

    if len(payload.text) > _MAX_PASTE_CHARS:
        raise ApiError(400, "text_too_long", f"貼り付けは{_MAX_PASTE_CHARS}文字までです。")

    result = ingest.ingest_conversation(
        conn,
        project_id=pid,
        uploaded_by=user.user_id,
        text=payload.text,
        recorded_at=payload.recorded_at,
        visibility=payload.visibility,
    )
    return {
        "source_id": result.source_id,
        "label_prefix": result.label_prefix,
        "version_no": result.version_no,
        "segment_count": result.segment_count,
        "redaction_count": result.redaction_count,
        "speaker_counts": result.speaker_counts,
    }


@router.post("/internal/projects/{pid}/sources/file")
def create_file_source(
    pid: UUID,
    file: UploadFile = File(...),
    recorded_at: datetime | None = Form(default=None),
    visibility: Visibility = Form(...),
    user: AuthenticatedUser = Depends(get_current_user),
    conn: psycopg.Connection = Depends(get_db_connection),
    settings: WorkerSettings = Depends(get_worker_settings),
    transport: httpx.BaseTransport | None = Depends(get_storage_transport),
) -> dict:
    """W9：ファイルのアップロード。再アップロードなら version_no が増える（§3.1、【C-3】）。"""

    _require_membership(conn, project_id=pid, user_id=user.user_id)

    filename = file.filename or ""
    suffix = Path(filename).suffix.lower()
    if suffix in _RESAVE_EXTENSIONS:
        raise ApiError(
            400,
            "unsupported_file_type",
            f"{suffix} は取り込めません。"
            "新しい形式（.xlsx / .docx / .pptx）で保存し直してください。",
        )
    if suffix not in _ALLOWED_EXTENSIONS:
        raise ApiError(
            400, "unsupported_file_type", f"{suffix or '(拡張子なし)'} は取り込めません。"
        )

    data = file.file.read()
    if len(data) > _MAX_FILE_BYTES:
        raise ApiError(400, "file_too_large", "ファイルは10MBまでです。")

    result = ingest.ingest_file(
        conn,
        project_id=pid,
        uploaded_by=user.user_id,
        filename=filename,
        data=data,
        recorded_at=recorded_at,
        visibility=visibility,
        settings=settings,
        storage_transport=transport,
    )
    return {
        "source_id": result.source_id,
        "label_prefix": result.label_prefix,
        "version_no": result.version_no,
        "segment_count": result.segment_count,
        "new_segment_count": result.new_segment_count,
        "redaction_count": result.redaction_count,
        "extraction_failed": result.extraction_failed,
        "truncated": result.truncated,
        "segment_limit": ingest.MAX_SEGMENTS_PER_FILE,
    }


@router.get("/internal/projects/{pid}/sources")
def list_sources(
    pid: UUID,
    user: AuthenticatedUser = Depends(get_current_user),
    conn: psycopg.Connection = Depends(get_db_connection),
) -> list[dict]:
    """W10：所属者の閲覧範囲（§1.3）で絞ったソース一覧。"""

    membership = _require_membership(conn, project_id=pid, user_id=user.user_id)
    return ingest.list_sources(conn, project_id=pid, membership=membership)


@router.get("/internal/sources/{sid}/download-url")
def get_download_url(
    sid: UUID,
    user: AuthenticatedUser = Depends(get_current_user),
    conn: psycopg.Connection = Depends(get_db_connection),
    settings: WorkerSettings = Depends(get_worker_settings),
    transport: httpx.BaseTransport | None = Depends(get_storage_transport),
) -> dict:
    """W13：pid を持たないので sid からプロジェクトを引く（§1.3）。見えなければ404。"""

    source = authz.fetch_source_with_project(conn, source_id=sid)
    if source is None:
        raise ApiError(404, "not_found", "ソースが見つかりません。")

    membership = authz.get_membership(conn, project_id=source.project_id, user_id=user.user_id)
    if membership is None or not authz.can_view_source(membership=membership, source=source):
        raise ApiError(404, "not_found", "ソースが見つかりません。")

    storage_path = ingest.fetch_current_storage_path(conn, version_id=source.current_version_id)
    if storage_path is None:
        raise ApiError(400, "no_file", "このソースにはダウンロードできるファイルがありません。")

    url, expires_at = storage.create_signed_url(
        storage_path, settings=settings, transport=transport
    )
    return {"url": url, "expires_at": expires_at}


@router.patch("/internal/sources/{sid}/visibility")
def update_source_visibility(
    sid: UUID,
    payload: VisibilityChangeRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    conn: psycopg.Connection = Depends(get_db_connection),
) -> dict:
    """W11：可視性の変更（権限表 §1.3）。監査ログの追記と ``visibility_epoch`` の +1 を

    同じトランザクションで行う。``managers_only`` → ``all`` のときは、そのソースの
    現在の版の区切りを全員向けの組で抽出し直すため ``consumed``・``carry_count`` を
    戻す【B-X1】。sid からプロジェクトを引く（見つからない・所属なしはどちらも404）。
    """

    source = _fetch_source_for_update(conn, source_id=sid)
    if source is None:
        raise ApiError(404, "not_found", "ソースが見つかりません。")
    membership = authz.get_membership(conn, project_id=source.project_id, user_id=user.user_id)
    if membership is None:
        raise ApiError(404, "not_found", "ソースが見つかりません。")

    if not _can_change_visibility(
        membership=membership, source=source, new_visibility=payload.visibility
    ):
        raise ApiError(403, "forbidden", "この操作を行う権限がありません。")

    old_visibility = source.visibility
    if payload.visibility != old_visibility:
        conn.execute(
            "UPDATE project_sources SET visibility = %s, updated_at = now() WHERE id = %s",
            (payload.visibility, sid),
        )
        _insert_audit_log(
            conn,
            source_id=sid,
            changed_by=user.user_id,
            field="visibility",
            old_value=old_visibility,
            new_value=payload.visibility,
            reason=payload.reason,
        )
        _bump_visibility_epoch(conn, source.project_id)
        if old_visibility == "managers_only" and payload.visibility == "all":
            # 公開に変えたときは、その版の区切りを全員向けの組で抽出し直す（§5.5、【B-X1】）。
            # トリガーが許す consumed・carry_count だけを更新する。
            conn.execute(
                "UPDATE source_segments SET consumed = false, carry_count = 0 "
                "WHERE source_version_id = %s",
                (source.current_version_id,),
            )

    return {"source": _source_view(conn, source_id=sid)}


@router.patch("/internal/sources/{sid}/exclusion")
def update_source_exclusion(
    sid: UUID,
    payload: ExclusionChangeRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    conn: psycopg.Connection = Depends(get_db_connection),
) -> dict:
    """W12：除外・除外の取り消し（権限表 §1.3、【C-4】）。

    除外の取り消しは、manager か、「本人が登録したソースで、かつ
    ``source_audit_log`` の ``is_excluded`` の最後の行が本人によるもの」のときだけ
    許す【C-4】。
    """

    source = _fetch_source_for_update(conn, source_id=sid)
    if source is None:
        raise ApiError(404, "not_found", "ソースが見つかりません。")
    membership = authz.get_membership(conn, project_id=source.project_id, user_id=user.user_id)
    if membership is None:
        raise ApiError(404, "not_found", "ソースが見つかりません。")

    if payload.is_excluded == source.is_excluded or payload.is_excluded:
        allowed = _can_exclude(membership=membership, source=source)
    else:
        allowed = _can_unexclude(conn, membership=membership, source=source)

    if not allowed:
        raise ApiError(403, "forbidden", "この操作を行う権限がありません。")

    old_is_excluded = source.is_excluded
    if payload.is_excluded != old_is_excluded:
        new_exclude_reason = payload.reason if payload.is_excluded else None
        conn.execute(
            "UPDATE project_sources SET is_excluded = %s, exclude_reason = %s, updated_at = now() "
            "WHERE id = %s",
            (payload.is_excluded, new_exclude_reason, sid),
        )
        _insert_audit_log(
            conn,
            source_id=sid,
            changed_by=user.user_id,
            field="is_excluded",
            old_value=str(old_is_excluded).lower(),
            new_value=str(payload.is_excluded).lower(),
            reason=payload.reason,
        )
        _bump_visibility_epoch(conn, source.project_id)

    return {"source": _source_view(conn, source_id=sid)}


@dataclass(frozen=True)
class _SourceForUpdate:
    """W11・W12 が権限判定に使う、行ロック済みのソースの現在の状態。"""

    id: UUID
    project_id: UUID
    uploaded_by: UUID
    visibility: Visibility
    is_excluded: bool
    current_version_id: UUID | None


def _fetch_source_for_update(
    conn: psycopg.Connection, *, source_id: UUID
) -> _SourceForUpdate | None:
    """sid からプロジェクトを引く（§1.3）。行ロックして、判定中の競合を防ぐ。"""

    row = conn.execute(
        "SELECT id, project_id, uploaded_by, visibility, is_excluded, current_version_id "
        "FROM project_sources WHERE id = %s FOR UPDATE",
        (source_id,),
    ).fetchone()
    if row is None:
        return None
    return _SourceForUpdate(
        id=row[0],
        project_id=row[1],
        uploaded_by=row[2],
        visibility=row[3],
        is_excluded=row[4],
        current_version_id=row[5],
    )


def _can_change_visibility(
    *, membership: Membership, source: _SourceForUpdate, new_visibility: Visibility
) -> bool:
    """権限表（§1.3）：``all`` → ``managers_only`` は登録者本人か manager、

    ``managers_only`` → ``all`` は manager だけ。可視性が変わらない要求は、
    実害が無いため登録者本人にも許す。
    """

    if membership.role == "manager":
        return True
    is_registrant = source.uploaded_by == membership.user_id
    if new_visibility == source.visibility:
        return is_registrant
    if source.visibility == "all" and new_visibility == "managers_only":
        return is_registrant
    return False  # managers_only → all は member には許さない


def _can_exclude(*, membership: Membership, source: _SourceForUpdate) -> bool:
    """権限表（§1.3）：除外は登録者本人か manager。"""

    if membership.role == "manager":
        return True
    return source.uploaded_by == membership.user_id


def _can_unexclude(
    conn: psycopg.Connection, *, membership: Membership, source: _SourceForUpdate
) -> bool:
    """権限表（§1.3）：除外の取り消しは manager か、登録者本人かつ最後の除外の

    操作が本人によるものだけ【C-4】。
    """

    if membership.role == "manager":
        return True
    if source.uploaded_by != membership.user_id:
        return False
    last_changed_by = _last_is_excluded_changed_by(conn, source_id=source.id)
    return last_changed_by == membership.user_id


def _last_is_excluded_changed_by(conn: psycopg.Connection, *, source_id: UUID) -> UUID | None:
    row = conn.execute(
        "SELECT changed_by FROM source_audit_log "
        "WHERE source_id = %s AND field = 'is_excluded' "
        "ORDER BY changed_at DESC LIMIT 1",
        (source_id,),
    ).fetchone()
    return row[0] if row else None


def _insert_audit_log(
    conn: psycopg.Connection,
    *,
    source_id: UUID,
    changed_by: UUID,
    field: Literal["visibility", "is_excluded"],
    old_value: str | None,
    new_value: str | None,
    reason: ExcludeReason,
) -> None:
    conn.execute(
        "INSERT INTO source_audit_log "
        "(source_id, changed_by, field, old_value, new_value, reason) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        (source_id, changed_by, field, old_value, new_value, reason),
    )


def _bump_visibility_epoch(conn: psycopg.Connection, project_id: UUID) -> None:
    """可視性・除外の変更で ``visibility_epoch`` を進め、次の実行での作り直しを予約する（§5.5）。"""

    conn.execute(
        "UPDATE projects SET visibility_epoch = visibility_epoch + 1, needs_rebuild = true "
        "WHERE id = %s",
        (project_id,),
    )


def _source_view(conn: psycopg.Connection, *, source_id: UUID) -> dict:
    row = conn.execute(
        """
        SELECT ps.id, ps.source_no, ps.type, ps.filename, ps.uploaded_by, ps.recorded_at,
               ps.visibility, ps.is_excluded, ps.exclude_reason, sv.version_no
        FROM project_sources ps
        LEFT JOIN source_versions sv ON sv.id = ps.current_version_id
        WHERE ps.id = %s
        """,
        (source_id,),
    ).fetchone()
    return {
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


def _require_membership(conn: psycopg.Connection, *, project_id: UUID, user_id: UUID) -> Membership:
    membership = authz.get_membership(conn, project_id=project_id, user_id=user_id)
    if membership is None:
        raise ApiError(404, "not_found", "プロジェクトが見つかりません。")
    return membership
