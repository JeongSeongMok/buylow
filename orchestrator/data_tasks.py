"""데이터 최신화 작업 — 수동 버튼(/data/update)과 스케줄러가 공유.

전체 시장(OHLCV+수급)을 마지막 적재일 다음날부터 증분 적재하고, 진행 상황을 잡 로그 파일에
남겨 작업 화면에서 실시간으로 볼 수 있게 한다.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from .lean.environment import REPO_ROOT


def run_data_update(job, data_dir: str) -> str:
    """JobManager.submit(name, lambda job: run_data_update(job, data_dir)) 형태로 사용."""
    from etl.universe import update_all_market

    log_path = REPO_ROOT / "runs" / f"update-{job.id}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    job.log_path = str(log_path)
    with open(log_path, "a", encoding="utf-8", buffering=1) as f:
        def on_progress(msg):
            f.write(f"{datetime.now():%H:%M:%S} {msg}\n")
        info = update_all_market(data_dir, on_progress=on_progress)
    return (f"OHLCV {info.get('price_tickers', 0)}종목 · "
            f"수급 {info.get('flow_ok', 0)}종목 · 펀더 {info.get('fund_ok', 0)}종목")
