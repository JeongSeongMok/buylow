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


@dataclass(frozen=True)
class MinuteBar:
    """분봉 1개. ms=자정(거래소 현지시각) 기준 밀리초, 가격은 원화 실제값."""

    ms: int
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


class KisSource:
    """한국투자증권(KIS) OpenAPI. 수정주가 일봉 제공(인증 필요 — BYO 키).

    주 용도는 "오늘(pykrx 미적재)" 데이터를 메우는 하이브리드 엣지지만, 임의 기간 일봉도
    조회 가능하다(100건/호출을 윈도로 분할). 가격은 원 단위 정수로 들어온다.
    """

    name = "kis"

    def __init__(self, client=None):
        # 클라이언트 주입 가능(테스트). 없으면 config의 KIS 자격증명으로 생성.
        self._client = client

    def _get_client(self):
        if self._client is None:
            from brokers.kis import from_config
            self._client = from_config()
        return self._client

    def fetch_daily(self, ticker: str, start: date, end: date) -> list[Bar]:
        rows = self._get_client().fetch_daily(ticker, start, end, adjusted=True)
        bars = [Bar(r["day"], float(r["open"]), float(r["high"]), float(r["low"]),
                    float(r["close"]), int(r["volume"])) for r in rows]
        return [b for b in bars if b.close > 0]


SOURCES = {"pykrx": PykrxSource, "fdr": FdrSource, "kis": KisSource}


def get_source(name: str = "pykrx") -> PriceSource:
    if name not in SOURCES:
        raise ValueError(f"알 수 없는 소스: {name} (가능: {list(SOURCES)})")
    return SOURCES[name]()
