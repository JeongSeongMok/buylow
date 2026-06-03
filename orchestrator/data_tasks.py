"""데이터 최신화 작업 — 수동 버튼(/data/update)과 스케줄러가 공유.

전체 시장(OHLCV+수급)을 마지막 적재일 다음날부터 증분 적재하고, 진행 상황을 잡 로그 파일에
남겨 작업 화면에서 실시간으로 볼 수 있게 한다.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from .lean.environment import REPO_ROOT


def run_minute_update(job, data_dir: str, tickers: list[str], days: int = 365) -> str:
    """선택한 종목의 분봉을 최근 `days`일(최대 약 1년) 적재. 이미 있는 날짜는 건너뛴다.

    JobManager.submit(name, lambda job: run_minute_update(job, data_dir, tickers, days)) 형태로 사용.
    """
    from datetime import date, timedelta
    from etl.kis_minute import ingest_minute, MAX_LOOKBACK_DAYS

    log_path = REPO_ROOT / "runs" / f"minute-{job.id}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    job.log_path = str(log_path)
    today = date.today()
    start = today - timedelta(days=min(days, MAX_LOOKBACK_DAYS))
    ok = total = skipped = 0
    with open(log_path, "a", encoding="utf-8", buffering=1) as f:
        def log(msg):
            f.write(f"{datetime.now():%H:%M:%S} {msg}\n")
        log(f"분봉 적재 시작: {len(tickers)}종목, {start}~{today}")
        for i, t in enumerate(tickers, 1):
            try:
                info = ingest_minute(t, start, today, data_dir, today=today)
                ok += 1
                total += info["bars"]
                skipped += info.get("skipped", 0)
                log(f"[{i}/{len(tickers)}] {t}: 신규 {info['days']}일 {info['bars']}개"
                    f"{' · 기존 ' + str(info['skipped']) + '일 건너뜀' if info.get('skipped') else ''}")
            except Exception as e:
                log(f"[{i}/{len(tickers)}] {t}: 실패 {type(e).__name__} {e}")
        log(f"완료: {ok}/{len(tickers)}종목, 신규 분봉 {total}개, 기존 {skipped}일 스킵")
    return f"분봉 {ok}/{len(tickers)}종목 · 신규 {total}개 · 기존 {skipped}일 스킵"


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
        try:  # 종목코드→이름 매핑(무인증, 1회) — 실패해도 데이터 적재엔 영향 없음
            from etl.names import fetch_and_save_names
            on_progress(f"종목명 {fetch_and_save_names(data_dir)}개 적재")
        except Exception as e:
            on_progress(f"종목명 적재 건너뜀: {type(e).__name__}")
    return (f"OHLCV {info.get('price_tickers', 0)}종목 · "
            f"수급 {info.get('flow_ok', 0)}종목 · 펀더 {info.get('fund_ok', 0)}종목")
