"""KRX 수급(투자자별 순매수) ETL.

pykrx 일자별 투자자별 **순매수 거래대금**(외국인/기관/개인, 단위 KRW, 음수=순매도)을 받아
LEAN 커스텀 데이터 입력 파일로 적재한다. 수급은 LEAN 표준 타입(TradeBar 등)이 아니므로
전략에선 커스텀 데이터(PythonData)로 소비할 예정 — 이 ETL은 그 입력 파일을 만든다.

주의: 수급 조회는 **KRX 로그인 필요**(config의 krx_id/krx_pw). 가격 OHLCV(etl/krx.py)와 달리 무인증 불가.
출력: data/krx/flow/<ticker>.csv  — 라인 `YYYYMMDD,foreign,institution,individual` (순매수 거래대금 KRW).
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
class FlowRecord:
    """하루치 투자자별 순매수 거래대금(KRW). 음수 = 순매도."""

    day: date
    foreign: int       # 외국인합계
    institution: int   # 기관합계
    individual: int    # 개인


def fetch_flow(ticker: str, start: date, end: date) -> list[FlowRecord]:
    """pykrx에서 투자자별 순매수 거래대금을 받아 정규화 (KRX 로그인 필요)."""
    from pykrx import stock
    df = stock.get_market_trading_value_by_date(
        start.strftime("%Y%m%d"), end.strftime("%Y%m%d"), ticker
    )
    return [
        FlowRecord(ts.date(), int(r["외국인합계"]), int(r["기관합계"]), int(r["개인"]))
        for ts, r in df.iterrows()
    ]


def flow_csv_path(data_dir: str | Path, ticker: str) -> Path:
    return Path(data_dir) / "krx" / "flow" / f"{ticker}.csv"


def write_flow(data_dir: str | Path, ticker: str, records: list[FlowRecord]) -> Path:
    out = flow_csv_path(data_dir, ticker)
    out.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f"{r.day:%Y%m%d},{r.foreign},{r.institution},{r.individual}"
        for r in sorted(records, key=lambda x: x.day)
    ]
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


def ingest_flow(ticker: str, start: date, end: date,
                data_dir: str | Path = DEFAULT_DATA_DIR) -> dict[str, Any]:
    """수급을 받아 적재. KRX 로그인 크리덴셜이 없으면 명확히 실패."""
    from orchestrator.config import apply_krx_credentials
    if not apply_krx_credentials():
        raise RuntimeError("수급 조회엔 KRX 로그인 필요 — config.local.yaml 또는 대시보드 /settings 에 krx_id/krx_pw 설정")
    records = fetch_flow(ticker, start, end)
    if not records:
        raise RuntimeError(f"{ticker}: 수급 데이터 없음 ({start}~{end})")
    path = write_flow(data_dir, ticker, records)
    return {
        "ticker": ticker,
        "rows": len(records),
        "first": min(r.day for r in records).isoformat(),
        "last": max(r.day for r in records).isoformat(),
        "path": str(path),
    }


def main() -> None:
    p = argparse.ArgumentParser(prog="python -m etl.flow", description="KRX 수급(투자자별 순매수) 적재")
    p.add_argument("--ticker", required=True)
    p.add_argument("--from", dest="start", required=True, type=date.fromisoformat)
    p.add_argument("--to", dest="end", default=None, type=date.fromisoformat, help="기본: 오늘")
    p.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    args = p.parse_args()
    info = ingest_flow(args.ticker, args.start, args.end or date.today(), Path(args.data_dir))
    print(f"수급 적재 완료: {info['ticker']} {info['rows']}행 ({info['first']}~{info['last']}) → {info['path']}")


if __name__ == "__main__":
    main()
