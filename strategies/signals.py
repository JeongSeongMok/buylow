# Signal 평가기 — 규칙 엔진의 부품(LEAN 지표 위 얇은 래퍼). 종목별로 생성되어 UP/DOWN/NONE 반환.
#
# 종류 추가 = 클래스 하나 + SIGNAL_TYPES 등록 + orchestrator/signals_catalog.py 스펙.
# 방향은 "상태"로 평가(매일 참/거짓) — AND/OR 결합이 의미를 갖도록.
#
# 선별 주기 2가지(intraday 플래그):
#  - 전날 종가 1회(intraday=False): LEAN 일봉 지표를 그대로 사용(기존 경로, 검증됨).
#  - 장중 매분(intraday=True): 완성된 일봉 종가 윈도우에 '현재가=잠정 오늘 종가'를 덧붙여
#    순수 지표(orchestrator.indicators)로 매분 재계산. 가격계열만 해당. 수급·가치는 장중 불변이라
#    전날값 유지(intraday 무시).
from collections import deque

from AlgorithmImports import *

from orchestrator.rules import UP, DOWN, NONE
from orchestrator import indicators as ind


class _DailyCloses:
    """완성된 일봉 종가 롤링 윈도우 — 분봉 구독에서 일봉 컨솔리데이터로 적재(장중 잠정평가용)."""

    def __init__(self, algo, symbol, maxlen=300):
        self.algo = algo
        self.symbol = symbol
        self.closes = deque(maxlen=maxlen)
        algo.consolidate(symbol, Resolution.DAILY, self._on_daily)

    def _on_daily(self, bar):
        if bar.close > 0:
            self.closes.append(float(bar.close))

    def vals(self):
        """어제까지 종가 + 현재가(잠정 오늘 종가). 현재가는 마지막 원소."""
        out = list(self.closes)
        price = float(self.algo.securities[self.symbol].price)
        if price > 0:
            out.append(price)
        return out


def _dir(up_cond, down_cond):
    return UP if up_cond else DOWN if down_cond else NONE


class EmaSignal:
    def __init__(self, algo, symbol, fast=12, slow=26, intraday=False):
        self.intraday = intraday
        self.fast_p, self.slow_p = int(fast), int(slow)
        if intraday:
            self.win = _DailyCloses(algo, symbol)
        else:
            self.f = algo.ema(symbol, self.fast_p, Resolution.DAILY)
            self.s = algo.ema(symbol, self.slow_p, Resolution.DAILY)

    def direction(self):
        if self.intraday:
            v = self.win.vals()
            f, s = ind.ema(v, self.fast_p), ind.ema(v, self.slow_p)
            return NONE if f is None or s is None else _dir(f > s, f < s)
        if not (self.f.is_ready and self.s.is_ready):
            return NONE
        return _dir(self.f.current.value > self.s.current.value,
                    self.f.current.value < self.s.current.value)


class MacdSignal:
    def __init__(self, algo, symbol, fast=12, slow=26, signal=9, intraday=False):
        self.intraday = intraday
        self.fast_p, self.slow_p, self.sig_p = int(fast), int(slow), int(signal)
        if intraday:
            self.win = _DailyCloses(algo, symbol)
        else:
            self.m = algo.macd(symbol, self.fast_p, self.slow_p, self.sig_p,
                               resolution=Resolution.DAILY)

    def direction(self):
        if self.intraday:
            res = ind.macd(self.win.vals(), self.fast_p, self.slow_p, self.sig_p)
            if res is None or res[1] is None:
                return NONE
            line, sig = res
            return _dir(line > sig, line < sig)
        if not self.m.is_ready:
            return NONE
        return _dir(self.m.current.value > self.m.signal.current.value,
                    self.m.current.value < self.m.signal.current.value)


class RsiSignal:
    def __init__(self, algo, symbol, period=14, oversold=30, overbought=70, intraday=False):
        self.intraday = intraday
        self.period = int(period)
        self.oversold = float(oversold)
        self.overbought = float(overbought)
        if intraday:
            self.win = _DailyCloses(algo, symbol)
        else:
            self.r = algo.rsi(symbol, self.period, resolution=Resolution.DAILY)

    def direction(self):
        if self.intraday:
            v = ind.rsi(self.win.vals(), self.period)
            return NONE if v is None else _dir(v < self.oversold, v > self.overbought)
        if not self.r.is_ready:
            return NONE
        v = self.r.current.value
        return _dir(v < self.oversold, v > self.overbought)


class MomentumSignal:
    def __init__(self, algo, symbol, lookback=60, intraday=False):
        self.intraday = intraday
        self.lookback = int(lookback)
        if intraday:
            self.win = _DailyCloses(algo, symbol)
        else:
            self.roc = algo.roc(symbol, self.lookback, Resolution.DAILY)

    def direction(self):
        if self.intraday:
            v = ind.roc(self.win.vals(), self.lookback)
            return NONE if v is None else _dir(v > 0, v < 0)
        if not self.roc.is_ready:
            return NONE
        return _dir(self.roc.current.value > 0, self.roc.current.value < 0)


class BollingerSignal:
    # 볼린저밴드 평균회귀 + 강한 돌파 시 스위칭(하이브리드).
    #  - 상단 터치~+switch% 미만: 과매수 → DOWN(평균회귀 매도)
    #  - 상단 +switch% 이상 강하게 돌파: UP(돌파 매수로 전환)
    #  - 하단 터치~−switch% 초과: 과매도 → UP(평균회귀 매수)
    #  - 하단 −switch% 이하 강하게 이탈: DOWN(돌파 매도로 전환)
    def __init__(self, algo, symbol, period=20, k=2.0, switch_pct=1.0, intraday=False):
        self.algo = algo
        self.symbol = symbol
        self.intraday = intraday
        self.period = int(period)
        self.k = float(k)
        self.sw = float(switch_pct) / 100.0  # 평균회귀 → 돌파 전환 임계
        if intraday:
            self.win = _DailyCloses(algo, symbol)
        else:
            self.bb = algo.bb(symbol, self.period, self.k, resolution=Resolution.DAILY)

    def _decide(self, price, upper, lower):
        if price >= upper:  # 상단 터치/돌파
            return UP if price >= upper * (1 + self.sw) else DOWN
        if price <= lower:  # 하단 터치/이탈
            return DOWN if price <= lower * (1 - self.sw) else UP
        return NONE         # 밴드 안

    def direction(self):
        if self.intraday:
            vals = self.win.vals()
            b = ind.bollinger(vals, self.period, self.k)
            if b is None:
                return NONE
            upper, _mid, lower = b
            return self._decide(vals[-1], upper, lower)  # vals[-1] = 현재가
        if not self.bb.is_ready:
            return NONE
        price = float(self.algo.securities[self.symbol].price)
        if price <= 0:
            return NONE
        return self._decide(price, self.bb.upper_band.current.value,
                            self.bb.lower_band.current.value)


class ValueSignal:
    # 저평가(가치) 필터. 차트가 아니라 KRX 펀더멘털 커스텀 데이터(PER/PBR/배당)를 읽는다.
    #  - 저PER·저PBR + ROE 기준 이상 + (선택)배당 기준 이상이면 UP(매수 후보), 아니면 NONE.
    #  - ROE는 별도 데이터 없이 PBR/PER로 파생(ROE = EPS/BPS = PBR/PER). 저PBR인데 ROE 낮은
    #    '가치 함정'을 걸러낸다. 가치는 매수 필터라 DOWN은 내지 않는다(타이밍 시그널과 AND로 조합).
    # 펀더멘털은 일 단위 데이터라 장중 불변 → intraday 무시(전날값 유지).
    def __init__(self, algo, symbol, per_max=10.0, pbr_max=1.0, roe_min=8.0, div_min=0.0,
                 intraday=False):
        from krx_data import KrxFundamental
        self.sec = algo.add_data(KrxFundamental, symbol.value, Resolution.DAILY)
        self.per_max = float(per_max)
        self.pbr_max = float(pbr_max)
        self.roe_min = float(roe_min)  # %
        self.div_min = float(div_min)  # 배당수익률 %

    def direction(self):
        d = self.sec.get_last_data()
        if d is None:
            return NONE
        per, pbr, div = d["per"], d["pbr"], d["div"]
        if not (0 < per <= self.per_max):   # 흑자 + 이익 대비 안 비쌈
            return NONE
        if not (0 < pbr <= self.pbr_max):   # 저PBR
            return NONE
        if (pbr / per) * 100.0 < self.roe_min:  # ROE(%)=PBR/PER → 가치 함정 제거
            return NONE
        if div < self.div_min:              # (선택) 배당 하한
            return NONE
        return UP


class FlowSignal:
    # 수급 추종. 선택한 투자자(외국인/기관/개인)의 최근 lookback일 누적 순매수 부호로 판단.
    #  - 누적 > 0 → UP(매수세 추종), 누적 < 0 → DOWN(매도세), 데이터 부족/미선택 → NONE.
    # KrxFlow 커스텀 데이터를 구독해 매 거래일 최신 순매수를 롤링 윈도우에 쌓는다(날짜 중복 방지).
    # 수급은 장 마감 후 집계라 장중 불변 → intraday 무시(전날값 유지).
    def __init__(self, algo, symbol, lookback=7, foreign=1, institution=1, individual=0,
                 intraday=False):
        from krx_data import KrxFlow
        self.sec = algo.add_data(KrxFlow, symbol.value, Resolution.DAILY)
        self.lookback = int(lookback)
        self.keys = [k for k, on in (("foreign", foreign), ("institution", institution),
                                     ("individual", individual)) if int(on)]
        self.window = deque(maxlen=self.lookback)
        self._last_day = None

    def direction(self):
        if not self.keys:
            return NONE
        d = self.sec.get_last_data()
        if d is not None and d.time != self._last_day:  # 새 거래일 수급만 적재(중복 방지)
            self._last_day = d.time
            self.window.append(sum(d[k] for k in self.keys))
        if len(self.window) < self.lookback:  # 워밍업
            return NONE
        net = sum(self.window)
        return _dir(net > 0, net < 0)


SIGNAL_TYPES = {
    "ema": EmaSignal,
    "macd": MacdSignal,
    "rsi": RsiSignal,
    "momentum": MomentumSignal,
    "bollinger": BollingerSignal,
    "value": ValueSignal,
    "flow": FlowSignal,
}


def build_signal(stype: str, params: dict, algo, symbol, intraday: bool = False):
    cls = SIGNAL_TYPES.get(stype)
    if cls is None:
        raise ValueError(f"알 수 없는 signal 타입: {stype}")
    return cls(algo, symbol, intraday=intraday, **params)
