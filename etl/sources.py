"""KRX 시세 소스 어댑터 (교체 가능).

각 소스를 표준 `Bar` 리스트로 정규화한다. 가격 OHLCV는 pykrx/FinanceDataReader 둘 다 무인증.
(펀더멘털 PER/PBR은 pykrx + KRX 로그인 필요 — 별도.) 나중에 토스 과거데이터 소스를 추가할 수 있다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Protocol


@dataclass(frozen=True)
class Bar:
    """일봉 1개 (가격은 원화 실제값; LEAN 스케일링은 lean_format에서)."""

    day: date
    open: float
    high: float
    low: float
    close: float
    volume: int


class PriceSource(Protocol):
    name: str
    def fetch_daily(self, ticker: str, start: date, end: date) -> list[Bar]: ...


class PykrxSource:
    """pykrx (KRX 데이터). OHLCV는 무인증."""

    name = "pykrx"

    def fetch_daily(self, ticker: str, start: date, end: date) -> list[Bar]:
        from pykrx import stock
        df = stock.get_market_ohlcv_by_date(
            start.strftime("%Y%m%d"), end.strftime("%Y%m%d"), ticker
        )
        bars = [
            Bar(ts.date(), float(r["시가"]), float(r["고가"]), float(r["저가"]),
                float(r["종가"]), int(r["거래량"]))
            for ts, r in df.iterrows()
        ]
        # 휴장/거래정지로 종가 0인 행 제외
        return [b for b in bars if b.close > 0]


class FdrSource:
    """FinanceDataReader (네이버/KRX 기반 대체 소스)."""

    name = "fdr"

    def fetch_daily(self, ticker: str, start: date, end: date) -> list[Bar]:
        import FinanceDataReader as fdr
        df = fdr.DataReader(ticker, start.isoformat(), end.isoformat())
        bars = [
            Bar(ts.date(), float(r["Open"]), float(r["High"]), float(r["Low"]),
                float(r["Close"]), int(r["Volume"]))
            for ts, r in df.iterrows()
        ]
        return [b for b in bars if b.close > 0]


SOURCES = {"pykrx": PykrxSource, "fdr": FdrSource}


def get_source(name: str = "pykrx") -> PriceSource:
    if name not in SOURCES:
        raise ValueError(f"알 수 없는 소스: {name} (가능: {list(SOURCES)})")
    return SOURCES[name]()
