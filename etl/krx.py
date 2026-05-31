"""KRX 가격 ETL — 소스에서 일봉을 받아 LEAN 포맷으로 ./data 에 적재.

LEAN은 이 데이터를 "읽기만" 한다. 외부 시세를 LEAN이 읽을 파일로 변환하는 건 우리(ETL) 몫.
(docs/ARCHITECTURE.md) 소스는 교체 가능(pykrx 기본, fdr 대체, 나중에 토스 과거데이터).

사용:
  python -m etl.krx --ticker 005930 --from 2020-01-01 --to 2024-12-31
  python -m etl.krx --ticker 005930 --from 2023-01-01 --source fdr
"""

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path
from typing import Any

from market.krx import KRX_MARKET, inject_krx_market

from .lean_format import write_equity_daily
from .sources import PriceSource, get_source

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA_DIR = REPO_ROOT / "data"


def ingest(
    ticker: str,
    start: date,
    end: date,
    data_dir: str | Path = DEFAULT_DATA_DIR,
    source: PriceSource | None = None,
) -> dict[str, Any]:
    """한 종목의 일봉을 받아 LEAN 포맷으로 적재하고 KRX 시장설정을 보장한다."""
    src = source or get_source()
    bars = src.fetch_daily(ticker, start, end)
    if not bars:
        raise RuntimeError(f"{ticker}: 받은 데이터 없음 ({start}~{end}, source={src.name})")
    path = write_equity_daily(data_dir, KRX_MARKET, ticker, bars)
    inject_krx_market(data_dir)  # market-hours/symbol-properties 보장
    return {
        "ticker": ticker,
        "source": src.name,
        "bars": len(bars),
        "first": min(b.day for b in bars).isoformat(),
        "last": max(b.day for b in bars).isoformat(),
        "path": str(path),
    }


def main() -> None:
    p = argparse.ArgumentParser(prog="python -m etl.krx", description="KRX 일봉 → LEAN 포맷 적재")
    p.add_argument("--ticker", required=True, help="6자리 종목코드 (예: 005930)")
    p.add_argument("--from", dest="start", required=True, type=date.fromisoformat)
    p.add_argument("--to", dest="end", default=None, type=date.fromisoformat,
                   help="기본: 오늘")
    p.add_argument("--source", default="pykrx", choices=list(("pykrx", "fdr")))
    p.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    args = p.parse_args()

    info = ingest(
        args.ticker, args.start, args.end or date.today(),
        Path(args.data_dir), get_source(args.source),
    )
    print(f"적재 완료: {info['ticker']} {info['bars']}개 ({info['first']}~{info['last']}) "
          f"source={info['source']} → {info['path']}")


if __name__ == "__main__":
    main()
