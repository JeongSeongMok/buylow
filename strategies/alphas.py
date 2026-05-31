# 전략 카탈로그 — 각 전략을 AlphaModel(신호 생성기)로 구현.
#
# 각 Alpha는 Insight(롱/청산 등)를 내고, 알고리즘의 PortfolioConstruction이 종목당 하나의
# 목표로 합산한다. 여러 Alpha를 add_alpha로 한 알고리즘에 결합 가능(= 멀티전략, 충돌 없음).
#
# 지금은 일봉(Resolution.DAILY) 가격 기반 전략들. 분봉/펀더멘털 전략은 데이터 갖춰지면 확장.
from AlgorithmImports import *


class EmaCrossAlpha(AlphaModel):
    """추세추종: 단기 EMA가 장기 EMA 위면 롱 신호."""

    def __init__(self, fast: int = 20, slow: int = 60, period_days: int = 5):
        self.fast = fast
        self.slow = slow
        self.period = timedelta(days=period_days)
        self._emas = {}

    def on_securities_changed(self, algorithm, changes):
        for sec in changes.added_securities:
            self._emas[sec.symbol] = (
                algorithm.ema(sec.symbol, self.fast, Resolution.DAILY),
                algorithm.ema(sec.symbol, self.slow, Resolution.DAILY),
            )
        for sec in changes.removed_securities:
            self._emas.pop(sec.symbol, None)

    def update(self, algorithm, data):
        insights = []
        for symbol, (fast, slow) in self._emas.items():
            if not (fast.is_ready and slow.is_ready):
                continue
            if fast.current.value > slow.current.value:  # 골든크로스 상태 → 롱
                insights.append(Insight.price(symbol, self.period, InsightDirection.UP))
        return insights


class BnfReversionAlpha(AlphaModel):
    """평균회귀(BNF): 이동평균 대비 -threshold 이상 과대낙폭이면 롱(반등 기대)."""

    def __init__(self, ma: int = 25, threshold: float = 0.12, period_days: int = 5):
        self.ma = ma
        self.threshold = threshold
        self.period = timedelta(days=period_days)
        self._smas = {}

    def on_securities_changed(self, algorithm, changes):
        for sec in changes.added_securities:
            self._smas[sec.symbol] = algorithm.sma(sec.symbol, self.ma, Resolution.DAILY)
        for sec in changes.removed_securities:
            self._smas.pop(sec.symbol, None)

    def update(self, algorithm, data):
        insights = []
        for symbol, sma in self._smas.items():
            if not sma.is_ready or symbol not in data.bars:
                continue
            price = data.bars[symbol].close
            if price < sma.current.value * (1 - self.threshold):  # 과대낙폭 → 롱
                insights.append(Insight.price(symbol, self.period, InsightDirection.UP))
        return insights
