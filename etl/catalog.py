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


def list_minute_tickers(data_dir: str | Path) -> list[str]:
    """분봉이 하루라도 적재된 종목코드 목록 (equity/<market>/minute/<ticker>/)."""
    d = Path(data_dir) / "equity" / KRX_MARKET / "minute"
    if not d.is_dir():
        return []
    return sorted(p.name for p in d.iterdir() if p.is_dir() and any(p.glob("*_trade.zip")))


def minute_day_count(data_dir: str | Path, ticker: str) -> int:
    """해당 종목의 분봉 적재 일수(저장된 일별 zip 개수)."""
    d = Path(data_dir) / "equity" / KRX_MARKET / "minute" / ticker.lower()
    return len(list(d.glob("*_trade.zip"))) if d.is_dir() else 0


def all_tickers(data_dir: str | Path) -> list[str]:
    return sorted(set(list_price_tickers(data_dir)) | set(list_flow_tickers(data_dir))
                  | set(list_minute_tickers(data_dir)))


def _last_csv_date(path: Path) -> str | None:
    if not path.exists():
        return None
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    if not lines:
        return None
    return datetime.strptime(lines[-1].split(",")[0], "%Y%m%d").date().isoformat()


def latest_loaded_date(data_dir: str | Path, kind: str = "price") -> str | None:
    """적재된 데이터의 최신 날짜(ISO). 없으면 None. kind: price | flow | fundamental.

    전 종목이 같은 거래일을 공유하므로 대표 종목(가능하면 005930) 1개만 읽어 판단.
    """
    if kind == "price":
        tickers = list_price_tickers(data_dir)
        if not tickers:
            return None
        ref = "005930" if "005930" in tickers else tickers[0]
        rows = read_price_daily(data_dir, ref)
        return rows[-1]["date"] if rows else None
    base = Path(data_dir) / "krx" / ("flow" if kind == "flow" else "fundamental")
    ref = base / "005930.csv"
    if not ref.exists():
        files = sorted(base.glob("*.csv")) if base.is_dir() else []
        if not files:
            return None
        ref = files[0]
    return _last_csv_date(ref)


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
