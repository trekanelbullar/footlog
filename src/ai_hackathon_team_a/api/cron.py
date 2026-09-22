"""定期実行の API（W17、設計書 §2.3・§5.7）。``X-Cron-Secret`` のみを受け付ける。"""

from fastapi import APIRouter, Depends

from ai_hackathon_team_a.api.deps import require_cron_secret
from ai_hackathon_team_a.cron import run_cron_tick
from ai_hackathon_team_a.db import get_pool
from ai_hackathon_team_a.worker_settings import WorkerSettings, get_worker_settings

router = APIRouter(dependencies=[Depends(require_cron_secret)])


@router.post("/internal/cron/tick")
def cron_tick(settings: WorkerSettings = Depends(get_worker_settings)) -> dict:
    """W17：Scheduler だけが呼ぶ。"""

    result = run_cron_tick(worker_settings=settings, pool=get_pool(settings))
    return {
        "projects_checked": result.projects_checked,
        "runs_executed": result.runs_executed,
        "runs_skipped_locked": result.runs_skipped_locked,
        "no_progress_alerts_sent": result.no_progress_alerts_sent,
    }
