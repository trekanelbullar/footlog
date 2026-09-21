"""worker の FastAPI アプリケーション（設計書 §1.2・§2）。

W1〜W7b・W8〜W10・W13・W14〜W16・W18・W19 は実装済み。W11・W12・W17・W20〜W23 は
以降の段で追加する。
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from ai_hackathon_team_a.api import projects, reports, runs, sources
from ai_hackathon_team_a.api.errors import register_error_handlers
from ai_hackathon_team_a.db import check_connected_as_app_worker, get_pool
from ai_hackathon_team_a.worker_settings import get_worker_settings


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """起動時に接続ユーザーを確かめる（追加指示 AD-5）。``APP_ENV=test`` のときだけ飛ばす。"""

    settings = get_worker_settings()
    if settings.app_env != "test":
        with get_pool(settings).connection() as conn:
            check_connected_as_app_worker(conn)
    yield


app = FastAPI(title="decision-trace worker", lifespan=lifespan)
register_error_handlers(app)
app.include_router(projects.router)
app.include_router(sources.router)
app.include_router(runs.router)
app.include_router(reports.router)


@app.get("/healthz")
def healthz() -> dict[str, bool]:
    """Cloud Run のヘルスチェック用。認証なし・データは返さない（W24）。"""

    return {"ok": True}
