"""KIS 분봉 ETL — 한국투자증권 OpenAPI에서 분봉을 받아 LEAN 분봉 포맷으로 ./data 에 적재.

분봉 백테스트(장중 타이밍 검증)용 데이터를 만든다. 일봉(과거 대량)은 pykrx로 충분하고,
분봉은 KIS만 제공하므로 별도 경로다.

⚠️ 제약(KIS): 과거 분봉은 당사 보관분(최대 약 1년)만, 호출당 120건 → 전체시장 장기 분봉은
비현실적. 유니버스를 좁히고 최근 구간만 적재하는 용도다(docs/ARCHITECTURE.md).

사용:
  python -m etl.kis_minute --ticker 005930 --from 2026-05-01 --to 2026-06-01
"""

from __future__ import annotations

import argparse
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from market.krx import KRX_MARKET, inject_krx_market

from .lean_format import write_equity_minute, equity_minute_zip_path
from .sources import MinuteBar

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA_DIR = REPO_ROOT / "data"

# KIS 분봉 보관 한계(약 1년). 그보다 과거는 빈 결과라 호출 자체를 막아 낭비를 줄인다.
MAX_LOOKBACK_DAYS = 365


def _to_minute_bars(rows: list[dict]) -> list[MinuteBar]:
    return [MinuteBar(r["ms"], float(r["open"]), float(r["high"]), float(r["low"]),
                      float(r["close"]), int(r["volume"]))
            for r in rows if r["close"] > 0]


def ingest_minute(
    ticker: str,
    start: date,
    end: date,
    data_dir: str | Path = DEFAULT_DATA_DIR,
    client=None,
    skip_existing: bool = True,
    today: date | None = None,
    on_progress=None,
    max_workers: int = 8,
) -> dict[str, Any]:
    """[start, end] 각 거래일의 분봉을 받아 하루 1개 zip으로 적재. 주말은 건너뛴다.

    - skip_existing: 이미 디스크에 있는 날짜는 API 호출 없이 건너뛴다(증분·재적재 비용 절감).
    - start는 KIS 보관 한계(약 1년)로 클램프된다 — 그보다 과거는 어차피 빈 결과.
    - on_progress(msg): 주기적 진행 보고(분봉 한 종목 적재가 수 분 걸리므로 하트비트용).
    - client 주입 가능(테스트). 없으면 config의 KIS 자격증명으로 생성.
    - max_workers: 거래일들을 병렬로 받는다(날짜끼리 독립이라 안전). 호출률은 client의 공유
      토큰버킷이 한도 내로 제한하므로 동시성은 네트워크 왕복 지연을 가리는 용도다.
    """
    if client is None:
        from brokers.kis import from_config
        client = from_config()

    today = today or date.today()
    floor = today - timedelta(days=MAX_LOOKBACK_DAYS)
    clamped = start < floor
    if clamped:
        start = floor

    weekdays = [start + timedelta(days=i) for i in range((end - start).days + 1)
                if (start + timedelta(days=i)).weekday() < 5]
    total = len(weekdays)
    # 이미 있는 날은 디스크 stat(싼 비용)으로 먼저 거르고, 실제 받을 날만 병렬 처리한다.
    to_fetch = [d for d in weekdays
                if not (skip_existing
                        and equity_minute_zip_path(data_dir, KRX_MARKET, ticker, d).exists())]
    days_skipped = total - len(to_fetch)

    days_written: list[str] = []
    total_bars = 0
    lock = threading.Lock()  # 집계(쓴 날·봉 수·진행 카운터) 보호 — 파일 쓰기는 날짜별로 독립
    processed = days_skipped  # 건너뛴 날은 이미 처리된 셈

    def report() -> None:
        if on_progress:
            on_progress(f"{ticker}: {processed}/{total}거래일 처리 (적재 {len(days_written)}일 "
                        f"{total_bars}개 · 기존 {days_skipped}일)")

    def fetch_day(d: date) -> int:
        rows = client.fetch_minute(ticker, d)
        bars = _to_minute_bars(rows)
        if not bars:
            return 0
        write_equity_minute(data_dir, KRX_MARKET, ticker, d, bars)
        with lock:
            days_written.append(d.isoformat())
        return len(bars)

    if to_fetch:
        workers = max(1, min(max_workers, len(to_fetch)))
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futures = {ex.submit(fetch_day, d): d for d in to_fetch}
            n = 0
            for fut in as_completed(futures):
                n_bars = fut.result()  # fetch_day 예외는 여기서 전파(전체 적재 실패로 보고)
                with lock:
                    total_bars += n_bars
                    processed += 1
                    n += 1
                    # 진행 하트비트: ~20거래일마다 — 분봉은 종목당 수백 호출이라 오래 걸린다.
                    beat = (n % 20 == 0)
                if beat:
                    report()
    report()  # 마지막 한 번(또는 to_fetch가 없을 때도)
    inject_krx_market(data_dir)
    days_written.sort()  # 병렬이라 완료 순서가 섞임 → first/last 위해 정렬
    return {"ticker": ticker, "days": len(days_written), "bars": total_bars,
            "skipped": days_skipped, "clamped": clamped,
            "first": days_written[0] if days_written else None,
            "last": days_written[-1] if days_written else None}


def main() -> None:
    p = argparse.ArgumentParser(prog="python -m etl.kis_minute",
                                description="KIS 분봉 → LEAN 분봉 포맷 적재")
    p.add_argument("--ticker", required=True, help="6자리 종목코드 (예: 005930)")
    p.add_argument("--from", dest="start", required=True, type=date.fromisoformat)
    p.add_argument("--to", dest="end", default=None, type=date.fromisoformat, help="기본: 오늘")
    p.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    args = p.parse_args()

    info = ingest_minute(args.ticker, args.start, args.end or date.today(), Path(args.data_dir))
    tail = f" (기존 {info['skipped']}일 건너뜀)" if info.get("skipped") else ""
    if info["days"]:
        print(f"적재 완료: {info['ticker']} {info['days']}일 {info['bars']}개 분봉 "
              f"({info['first']}~{info['last']}){tail}")
    else:
        print(f"신규 적재 분봉 없음: {info['ticker']}{tail} — KIS 보관기간(약 1년) 밖이거나 데이터 없음")


if __name__ == "__main__":
    main()
