# 멀티전략 결합 예시 — 추세추종(EMA 교차) + 평균회귀(BNF)를 한 포트폴리오로.
#
# 두 Alpha가 각자 신호(Insight)를 내고, EqualWeightingPortfolioConstructionModel이
# 종목당 하나의 목표 비중으로 합산한다. → 한 전략이 산 걸 다른 전략이 임의로 파는 충돌 없음.
# 데이터: etl/krx.py 로 적재한 ./data 의 한국 일봉(예: 005930).
from AlgorithmImports import *

from krx_framework import KrxFrameworkAlgorithm
from alphas import EmaCrossAlpha, BnfReversionAlpha


class KrxFrameworkExample(KrxFrameworkAlgorithm):
    def initialize(self):
        self.set_start_date(2023, 1, 2)
        self.set_end_date(2023, 12, 28)
        self.set_cash(10_000_000)  # 1천만 원

        self.setup_krx_framework()  # krx 시장·KRW·수수료모델·결합/실행 기본값

        symbols = self.krx_symbols(["005930"])
        self.set_universe_selection(ManualUniverseSelectionModel(symbols))
        self.set_benchmark(symbols[0])  # 미국 SPY 의존 회피

        # 두 전략(Alpha)을 결합
        self.add_alpha(EmaCrossAlpha())
        self.add_alpha(BnfReversionAlpha())
