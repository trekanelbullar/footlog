"""worker の FastAPI アプリケーション（設計書 §1.2・§2）。

W1〜W23 の本実装は2段目以降で追加する。ここでは認証の通しを確かめるための
最小限のルートだけを持つ：認証不要の ``/healthz``（W24）と、DB に触れない
仮実装の ``/internal/me/projects``（W1）。
"""

from fastapi import Depends, FastAPI

from ai_hackathon_team_a.api.deps import get_current_user, require_worker_secret
from ai_hackathon_team_a.api.errors import register_error_handlers
from ai_hackathon_team_a.auth import AuthenticatedUser

app = FastAPI(title="decision-trace worker")
register_error_handlers(app)


@app.get("/healthz")
def healthz() -> dict[str, bool]:
    """Cloud Run のヘルスチェック用。認証なし・データは返さない（W24）。"""

    return {"ok": True}


@app.get(
    "/internal/me/projects",
    dependencies=[Depends(require_worker_secret)],
)
def list_my_projects(user: AuthenticatedUser = Depends(get_current_user)) -> list[dict]:
    """W1 の仮実装。DB に触れず、認証（共有シークレット＋JWT）の通しだけを確かめる。"""

    return []
