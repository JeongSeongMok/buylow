# 한국 특화 커스텀 Alpha (LEAN 내장에 없는 것).
#
# 표준 기술적 신호는 LEAN 내장(alphas.build_alpha)을 쓰고, 여기엔 LEAN이 안 주는 한국 특화만 둔다.
from collections import deque

from AlgorithmImports import *

from krx_data import KrxFlow, KrxFundamental


class FlowFollowingAlpha(AlphaModel):
    """수급 추종: 외국인 순매수 최근 lookback일 누적이 양수면 롱.

    수급(KrxFlow 커스텀 데이터)을 종목별로 구독해 외국인 순매수를 롤링 합산한다.
    순매수 강도를 Insight weight로 실어 InsightWeighting PCM과도 호환되게 한다.
    """

    def __init__(self, lookback: int = 5, period_days: int = 5):
        self.lookback = lookback
        self.hold = timedelta(days=period_days)
        self._flow_sym = {}   # equity symbol -> flow data symbol
        self._window = {}     # equity symbol -> deque(외국인 순매수)
        # 강도를 weight로 실어 InsightWeighting PCM과 호환하려면 여기서 정규화 후 Insight에 weight 전달.
        # 지금 기본 PCM(EqualWeighting)은 weight를 무시하므로 단순 UP만 방출한다.

    def on_securities_changed(self, algorithm, changes):
        for sec in changes.added_securities:
            eq = sec.symbol
            if eq.security_type != SecurityType.EQUITY:
                continue
            # 해당 종목의 수급 커스텀 데이터 구독
            self._flow_sym[eq] = algorithm.add_data(KrxFlow, eq.value, Resolution.DAILY).symbol
            self._window[eq] = deque(maxlen=self.lookback)
        for sec in changes.removed_securities:
            self._flow_sym.pop(sec.symbol, None)
            self._window.pop(sec.symbol, None)

    def update(self, algorithm, data):
        insights = []
        for eq, fs in self._flow_sym.items():
            if data.contains_key(fs) and data[fs] is not None:
                self._window[eq].append(data[fs]["foreign"])
            window = self._window[eq]
            if len(window) == window.maxlen and sum(window) > 0:  # 외국인 누적 순매수 → 롱
                insights.append(Insight.price(eq, self.hold, InsightDirection.UP))
        return insights


class ValueAlpha(AlphaModel):
    """저PBR 가치: PBR이 max_pbr 미만이면 롱(저평가). 가치는 장기라 보유기간을 길게.

    PER/PBR(KrxFundamental 커스텀 데이터)을 종목별로 구독해 최신 PBR로 판단한다.
    """

    def __init__(self, max_pbr: float = 1.0, period_days: int = 20):
        self.max_pbr = max_pbr
        self.hold = timedelta(days=period_days)
        self._fund_sym = {}   # equity symbol -> fundamental data symbol
        self._pbr = {}        # equity symbol -> 최신 PBR

    def on_securities_changed(self, algorithm, changes):
        for sec in changes.added_securities:
            eq = sec.symbol
            if eq.security_type != SecurityType.EQUITY:
                continue
            self._fund_sym[eq] = algorithm.add_data(KrxFundamental, eq.value, Resolution.DAILY).symbol
        for sec in changes.removed_securities:
            self._fund_sym.pop(sec.symbol, None)
            self._pbr.pop(sec.symbol, None)

    def update(self, algorithm, data):
        insights = []
        for eq, fs in self._fund_sym.items():
            if data.contains_key(fs) and data[fs] is not None:
                self._pbr[eq] = data[fs]["pbr"]
            pbr = self._pbr.get(eq)
            if pbr is not None and 0 < pbr < self.max_pbr:  # 저PBR → 저평가 → 롱
                insights.append(Insight.price(eq, self.hold, InsightDirection.UP))
        return insights
