"""적재된 ./data 를 종목별로 조회/요약 (대시보드 표시용).

ETL이 만든 LEAN 포맷 파일을 읽어 사람이 보기 좋게 되돌린다(가격은 ×10000 역스케일).
읽기 전용 — 데이터를 쓰는 건 etl.krx / etl.flow 다.
"""

from __future__ import annotations

import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

from market.krx import KRX_MARKET

from .flow import flow_csv_path
from .lean_format import PRICE_SCALE, equity_daily_zip_path


def list_price_tickers(data_dir: str | Path) -> list[str]:
    d = Path(data_dir) / "equity" / KRX_MARKET / "daily"
    return sorted(p.stem for p in d.glob("*.zip")) if d.is_dir() else []


def list_flow_tickers(data_dir: str | Path) -> list[str]:
    d = Path(data_dir) / "krx" / "flow"
    return sorted(p.stem for p in d.glob("*.csv")) if d.is_dir() else []


def all_tickers(data_dir: str | Path) -> list[str]:
    return sorted(set(list_price_tickers(data_dir)) | set(list_flow_tickers(data_dir)))


def read_price_daily(data_dir: str | Path, ticker: str) -> list[dict[str, Any]]:
    zp = equity_daily_zip_path(data_dir, KRX_MARKET, ticker)
    if not zp.exists():
        return []
    with zipfile.ZipFile(zp) as zf:
        text = zf.read(f"{ticker}.csv").decode("utf-8")
    rows = []
    for line in text.strip().splitlines():
        dt, o, h, l, c, v = line.split(",")
        rows.append({
            "date": datetime.strptime(dt.split()[0], "%Y%m%d").date().isoformat(),
            "open": int(o) / PRICE_SCALE, "high": int(h) / PRICE_SCALE,
            "low": int(l) / PRICE_SCALE, "close": int(c) / PRICE_SCALE,
            "volume": int(v),
        })
    return rows


def read_flow(data_dir: str | Path, ticker: str) -> list[dict[str, Any]]:
    fp = flow_csv_path(data_dir, ticker)
    if not fp.exists():
        return []
    rows = []
    for line in fp.read_text(encoding="utf-8").strip().splitlines():
        dt, f, i, p = line.split(",")
        rows.append({
            "date": datetime.strptime(dt, "%Y%m%d").date().isoformat(),
            "foreign": int(f), "institution": int(i), "individual": int(p),
        })
    return rows


def _summarize(rows: list[dict], recent: int) -> dict[str, Any]:
    if not rows:
        return {"count": 0, "first": None, "last": None, "recent": []}
    return {
        "count": len(rows),
        "first": rows[0]["date"],
        "last": rows[-1]["date"],
        "recent": list(reversed(rows[-recent:])),  # 최신순
    }


def ticker_summary(data_dir: str | Path, ticker: str, recent: int = 15) -> dict[str, Any]:
    return {
        "ticker": ticker,
        "price": _summarize(read_price_daily(data_dir, ticker), recent),
        "flow": _summarize(read_flow(data_dir, ticker), recent),
    }
