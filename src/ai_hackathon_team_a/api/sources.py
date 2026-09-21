"""ソースの登録・取り込み・一覧・ダウンロードURL の API（W8〜W10、W13、設計書 §2.2・§4）。"""

from datetime import datetime
from pathlib import Path
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
_ALLOWED_EXTENSIONS = {".txt", ".md", ".csv", ".py", ".ts", ".js", ".json", ".xlsx", ".pdf"}
_MAX_FILE_BYTES = 10 * 1024 * 1024
_MAX_PASTE_CHARS = 200_000


class ConversationSourceRequest(BaseModel):
    text: str = Field(min_length=1)
    recorded_at: datetime | None = None
    visibility: Visibility


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


def _require_membership(conn: psycopg.Connection, *, project_id: UUID, user_id: UUID) -> Membership:
    membership = authz.get_membership(conn, project_id=project_id, user_id=user_id)
    if membership is None:
        raise ApiError(404, "not_found", "プロジェクトが見つかりません。")
    return membership
