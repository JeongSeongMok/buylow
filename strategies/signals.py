# Signal 평가기 — 규칙 엔진의 부품(LEAN 지표 위 얇은 래퍼). 종목별로 생성되어 UP/DOWN/NONE 반환.
#
# 종류 추가 = 클래스 하나 + SIGNAL_TYPES 등록 + orchestrator/signals_catalog.py 스펙.
# 방향은 "상태"로 평가(매일 참/거짓) — AND/OR 결합이 의미를 갖도록.
from AlgorithmImports import *

from orchestrator.rules import UP, DOWN, NONE


class EmaSignal:
    def __init__(self, algo, symbol, fast=12, slow=26):
        self.f = algo.ema(symbol, int(fast), Resolution.DAILY)
        self.s = algo.ema(symbol, int(slow), Resolution.DAILY)

    def direction(self):
        if not (self.f.is_ready and self.s.is_ready):
            return NONE
        if self.f.current.value > self.s.current.value:
            return UP
        if self.f.current.value < self.s.current.value:
            return DOWN
        return NONE


class MacdSignal:
    def __init__(self, algo, symbol, fast=12, slow=26, signal=9):
        self.m = algo.macd(symbol, int(fast), int(slow), int(signal), resolution=Resolution.DAILY)

    def direction(self):
        if not self.m.is_ready:
            return NONE
        if self.m.current.value > self.m.signal.current.value:
            return UP
        if self.m.current.value < self.m.signal.current.value:
            return DOWN
        return NONE


class RsiSignal:
    def __init__(self, algo, symbol, period=14, oversold=30, overbought=70):
        self.r = algo.rsi(symbol, int(period), resolution=Resolution.DAILY)
        self.oversold = float(oversold)
        self.overbought = float(overbought)

    def direction(self):
        if not self.r.is_ready:
            return NONE
        v = self.r.current.value
        if v < self.oversold:
            return UP
        if v > self.overbought:
            return DOWN
        return NONE


class MomentumSignal:
    def __init__(self, algo, symbol, lookback=60):
        self.roc = algo.roc(symbol, int(lookback), Resolution.DAILY)

    def direction(self):
        if not self.roc.is_ready:
            return NONE
        if self.roc.current.value > 0:
            return UP
        if self.roc.current.value < 0:
            return DOWN
        return NONE


class BollingerSignal:
    # 볼린저밴드 평균회귀 + 강한 돌파 시 스위칭(하이브리드).
    #  - 상단 터치~+switch% 미만: 과매수 → DOWN(평균회귀 매도)
    #  - 상단 +switch% 이상 강하게 돌파: UP(돌파 매수로 전환)
    #  - 하단 터치~−switch% 초과: 과매도 → UP(평균회귀 매수)
    #  - 하단 −switch% 이하 강하게 이탈: DOWN(돌파 매도로 전환)
    def __init__(self, algo, symbol, period=20, k=2.0, switch_pct=1.0):
        self.algo = algo
        self.symbol = symbol
        self.bb = algo.bb(symbol, int(period), float(k), resolution=Resolution.DAILY)
        self.sw = float(switch_pct) / 100.0  # 평균회귀 → 돌파 전환 임계

    def direction(self):
        if not self.bb.is_ready:
            return NONE
        price = float(self.algo.securities[self.symbol].price)
        if price <= 0:
            return NONE
        upper = self.bb.upper_band.current.value
        lower = self.bb.lower_band.current.value
        if price >= upper:  # 상단 터치/돌파
            return UP if price >= upper * (1 + self.sw) else DOWN  # 강한 돌파면 매수, 단순 터치면 매도
        if price <= lower:  # 하단 터치/이탈
            return DOWN if price <= lower * (1 - self.sw) else UP  # 강한 이탈이면 매도, 단순 터치면 매수
        return NONE         # 밴드 안


SIGNAL_TYPES = {
    "ema": EmaSignal,
    "macd": MacdSignal,
    "rsi": RsiSignal,
    "momentum": MomentumSignal,
    "bollinger": BollingerSignal,
}


def build_signal(stype: str, params: dict, algo, symbol):
    cls = SIGNAL_TYPES.get(stype)
    if cls is None:
        raise ValueError(f"알 수 없는 signal 타입: {stype}")
    return cls(algo, symbol, **params)
