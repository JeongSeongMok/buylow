"""일일 증분 적재 스케줄러 (APScheduler).

config의 scheduler.enabled가 켜져 있으면, 평일 지정 시각(KST)에 유니버스 증분 갱신을
백그라운드 잡으로 던진다. 비활성이면 아무것도 안 함(사용자가 켜야 자동 적재).
"""

from __future__ import annotations

from typing import Any

from .config import get_data_folder, get_scheduler_config
from .jobs import JobManager


def start_scheduler(jobs: JobManager) -> Any | None:
    cfg = get_scheduler_config()
    if not cfg["enabled"]:
        return None

    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger

    from etl.universe import update_universe

    market = cfg["market"]

    def _daily():
        jobs.submit(
            f"daily {market} update",
            lambda job: update_universe(market, get_data_folder()),
        )

    sched = BackgroundScheduler(timezone="Asia/Seoul")
    sched.add_job(_daily, CronTrigger(day_of_week="mon-fri", hour=cfg["hour"], minute=0),
                  id="daily-universe-update", replace_existing=True)
    sched.start()
    return sched
