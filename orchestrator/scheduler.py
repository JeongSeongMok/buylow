"""일일 증분 적재 스케줄러 (APScheduler).

config의 scheduler.enabled가 켜져 있으면, 평일 지정 시각(KST)에 '데이터 최신화'(대시보드 버튼과
동일 — 전체 시장 OHLCV+수급 증분)를 백그라운드 잡으로 던진다. 비활성이면 아무것도 안 함.
"""

from __future__ import annotations

from typing import Any

from .config import get_data_folder, get_scheduler_config
from .data_tasks import run_data_update
from .jobs import JobManager


def start_scheduler(jobs: JobManager) -> Any | None:
    cfg = get_scheduler_config()
    if not cfg["enabled"]:
        return None

    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger

    def _daily():
        jobs.submit("데이터 최신화 (자동)",
                    lambda job: run_data_update(job, get_data_folder()))

    sched = BackgroundScheduler(timezone="Asia/Seoul")
    sched.add_job(_daily, CronTrigger(day_of_week="mon-fri", hour=cfg["hour"], minute=0),
                  id="daily-data-update", replace_existing=True)
    sched.start()
    return sched
