"""자동 증분 적재 스케줄러 (APScheduler).

config의 scheduler.enabled(기본 켜짐)면, 서버 가동 중 `interval_minutes`(기본 30분) 간격으로
'데이터 최신화'(대시보드 버튼과 동일 — 전체 시장 OHLCV+수급+펀더 증분, pykrx)를 반복 실행한다.
이미 채워져 있으면 증분이라 금방 끝나므로 짧은 간격 연속 실행도 부담이 적다(사용자 선택).
`scheduler.minute_universe`에 종목이 있으면 같은 잡에서 그 종목들의 분봉(KIS)도 증분 적재한다.

설계상 일봉(pykrx, 키리스)과 분봉(KIS)을 한 잡에서 순차 실행한다 — 분봉은 대상종목이 지정된
경우에만. 동시에 두 번 돌지 않게 max_instances=1, coalesce로 밀린 실행은 합친다.
"""

from __future__ import annotations

from typing import Any

from .config import get_data_folder, get_scheduler_config
from .data_tasks import run_data_update, run_minute_update
from .jobs import JobManager


def run_scheduled(jobs: JobManager) -> None:
    """스케줄러 한 틱 — 일봉(pykrx) 증분 + (대상종목이 있으면) 분봉 증분을 백그라운드 잡으로 던진다.
    매 틱마다 config를 다시 읽어, 분봉 대상종목 변경이 재시작 없이 반영되게 한다."""
    data_dir = get_data_folder()
    jobs.submit("데이터 최신화 (자동)",
                lambda job: run_data_update(job, data_dir))
    minute_uni = get_scheduler_config()["minute_universe"]
    if minute_uni:
        jobs.submit("분봉 최신화 (자동)",
                    lambda job: run_minute_update(job, data_dir, minute_uni))


def start_scheduler(jobs: JobManager) -> Any | None:
    cfg = get_scheduler_config()
    if not cfg["enabled"]:
        return None

    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.interval import IntervalTrigger

    def _tick():
        run_scheduled(jobs)

    sched = BackgroundScheduler(timezone="Asia/Seoul")
    # 짧은 간격 연속 실행 — 겹침 방지(max_instances=1) + 밀린 실행 합치기(coalesce)
    sched.add_job(_tick, IntervalTrigger(minutes=cfg["interval_minutes"]),
                  id="auto-data-update", replace_existing=True,
                  max_instances=1, coalesce=True)
    sched.start()
    return sched
