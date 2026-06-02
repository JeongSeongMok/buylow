"""KRX 펀더멘털(PER/PBR/배당) ETL.

pykrx 일자별 투자지표(BPS/PER/PBR/EPS/DIV/DPS)를 받아 LEAN 커스텀 데이터 입력 파일로 적재한다.
KRX가 발표하는 as-of 값이라 raw 재무제표(OpenDART)의 공시일 정합성 문제는 작다. 가치/팩터 전략용.

주의: **KRX 로그인 필요**(config의 krx_id/krx_pw). 가격 OHLCV(etl.krx)와 달리 무인증 불가.
출력: data/krx/fundamental/<ticker>.csv — 라인 `YYYYMMDD,per,pbr,div`.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA_DIR = REPO_ROOT / "data"


@dataclass(frozen=True)
class FundamentalRecord:
    day: date
    per: float
    pbr: float
    div: float   # 배당수익률(%)


def fetch_fundamental(ticker: str, start: date, end: date) -> list[FundamentalRecord]:
    """pykrx 투자지표 조회 (KRX 로그인 필요)."""
    from pykrx import stock
    df = stock.get_market_fundamental_by_date(
        start.strftime("%Y%m%d"), end.strftime("%Y%m%d"), ticker
    )
    return [
        FundamentalRecord(ts.date(), float(r["PER"]), float(r["PBR"]), float(r["DIV"]))
        for ts, r in df.iterrows()
    ]


def fundamental_csv_path(data_dir: str | Path, ticker: str) -> Path:
    return Path(data_dir) / "krx" / "fundamental" / f"{ticker}.csv"


def _read_fundamental_records(data_dir: str | Path, ticker: str) -> list[FundamentalRecord]:
    fp = fundamental_csv_path(data_dir, ticker)
    if not fp.exists():
        return []
    out = []
    for line in fp.read_text(encoding="utf-8").strip().splitlines():
        d, per, pbr, div = line.split(",")
        out.append(FundamentalRecord(datetime.strptime(d, "%Y%m%d").date(),
                                     float(per), float(pbr), float(div)))
    return out


def write_fundamental(data_dir: str | Path, ticker: str, records: list[FundamentalRecord],
                      merge: bool = False) -> Path:
    if merge:  # 기존 펀더멘털과 날짜 기준 병합(증분 갱신 시 과거 보존)
        by_day = {r.day: r for r in _read_fundamental_records(data_dir, ticker)}
        for r in records:
            by_day[r.day] = r
        records = list(by_day.values())
    out = fundamental_csv_path(data_dir, ticker)
    out.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"{r.day:%Y%m%d},{r.per},{r.pbr},{r.div}" for r in sorted(records, key=lambda x: x.day)]
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


def ingest_fundamental(ticker: str, start: date, end: date,
                       data_dir: str | Path = DEFAULT_DATA_DIR, merge: bool = False) -> dict[str, Any]:
    from orchestrator.config import apply_krx_credentials
    if not apply_krx_credentials():
        raise RuntimeError("펀더멘털 조회엔 KRX 로그인 필요 — config.local.yaml 또는 /settings 에 krx_id/krx_pw 설정")
    records = fetch_fundamental(ticker, start, end)
    if not records:
        raise RuntimeError(f"{ticker}: 펀더멘털 데이터 없음 ({start}~{end})")
    path = write_fundamental(data_dir, ticker, records, merge=merge)
    return {
        "ticker": ticker, "rows": len(records),
        "first": min(r.day for r in records).isoformat(),
        "last": max(r.day for r in records).isoformat(),
        "path": str(path),
    }


def ingest_fundamental_universe(start: date, end: date, data_dir: str | Path = DEFAULT_DATA_DIR,
                                *, merge: bool = True, on_progress=None) -> dict[str, Any]:
    """전 종목 펀더멘털을 **날짜별 단면**(get_market_fundamental)으로 효율 적재.

    종목당 1회(2,800+콜) 대신 거래일당 1회 → 빠르고, 데이터 없는 종목의 빈 응답 에러도 없다.
    수급(per-ticker)과 달리 단면 API가 있어 가능. KRX 로그인 필요.
    """
    from pykrx import stock
    from orchestrator.config import apply_krx_credentials
    if not apply_krx_credentials():
        raise RuntimeError("펀더멘털 조회엔 KRX 로그인 필요 — config.local.yaml 또는 /settings 에 krx_id/krx_pw 설정")

    def progress(msg):
        if on_progress:
            on_progress(msg)

    # 실제 거래일만 순회(휴장일 콜 낭비 방지) — 기준 종목(삼성)의 거래일 인덱스
    cal = stock.get_market_ohlcv_by_date(start.strftime("%Y%m%d"), end.strftime("%Y%m%d"), "005930")
    trading_days = [ts.date() for ts in cal.index]
    series: dict[str, list[FundamentalRecord]] = defaultdict(list)
    for i, d in enumerate(trading_days, 1):
        df = stock.get_market_fundamental(d.strftime("%Y%m%d"), market="ALL")
        if df is not None and not df.empty:
            for tkr, r in df.iterrows():
                series[tkr].append(FundamentalRecord(d, float(r["PER"]), float(r["PBR"]), float(r["DIV"])))
        if i % 20 == 0 or i == len(trading_days):
            progress(f"펀더멘털 수집 {i}/{len(trading_days)}거래일")
    for tkr, recs in series.items():
        write_fundamental(data_dir, tkr, recs, merge=merge)
    return {"tickers": len(series), "trading_days": len(trading_days)}


def main() -> None:
    p = argparse.ArgumentParser(prog="python -m etl.fundamental", description="KRX 펀더멘털(PER/PBR) 적재")
    p.add_argument("--ticker", required=True)
    p.add_argument("--from", dest="start", required=True, type=date.fromisoformat)
    p.add_argument("--to", dest="end", default=None, type=date.fromisoformat, help="기본: 오늘")
    p.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    args = p.parse_args()
    info = ingest_fundamental(args.ticker, args.start, args.end or date.today(), Path(args.data_dir))
    print(f"펀더멘털 적재 완료: {info['ticker']} {info['rows']}행 ({info['first']}~{info['last']}) → {info['path']}")


if __name__ == "__main__":
    main()
