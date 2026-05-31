"""KRX 펀더멘털(PER/PBR/배당) ETL.

pykrx 일자별 투자지표(BPS/PER/PBR/EPS/DIV/DPS)를 받아 LEAN 커스텀 데이터 입력 파일로 적재한다.
KRX가 발표하는 as-of 값이라 raw 재무제표(OpenDART)의 공시일 정합성 문제는 작다. 가치/팩터 전략용.

주의: **KRX 로그인 필요**(config의 krx_id/krx_pw). 가격 OHLCV(etl.krx)와 달리 무인증 불가.
출력: data/krx/fundamental/<ticker>.csv — 라인 `YYYYMMDD,per,pbr,div`.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date
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


def write_fundamental(data_dir: str | Path, ticker: str, records: list[FundamentalRecord]) -> Path:
    out = fundamental_csv_path(data_dir, ticker)
    out.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"{r.day:%Y%m%d},{r.per},{r.pbr},{r.div}" for r in sorted(records, key=lambda x: x.day)]
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


def ingest_fundamental(ticker: str, start: date, end: date,
                       data_dir: str | Path = DEFAULT_DATA_DIR) -> dict[str, Any]:
    from orchestrator.config import apply_krx_credentials
    if not apply_krx_credentials():
        raise RuntimeError("펀더멘털 조회엔 KRX 로그인 필요 — config.local.yaml 또는 /settings 에 krx_id/krx_pw 설정")
    records = fetch_fundamental(ticker, start, end)
    if not records:
        raise RuntimeError(f"{ticker}: 펀더멘털 데이터 없음 ({start}~{end})")
    path = write_fundamental(data_dir, ticker, records)
    return {
        "ticker": ticker, "rows": len(records),
        "first": min(r.day for r in records).isoformat(),
        "last": max(r.day for r in records).isoformat(),
        "path": str(path),
    }


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
