# 범용 조합 전략 — 대시보드에서 고른 Alpha 조합을 동적으로 구성해 실행.
#
# 'composition' 파라미터(JSON)를 읽어 여러 Alpha를 결합한다. 전략 레지스트리(대시보드)가
# 조합 스펙을 만들어 이 알고리즘에 넘기는 방식 → .py를 새로 안 짜도 조합을 백테스트.
#
# composition 예:
# {"alphas":[{"name":"ema_cross","params":{"fast":20,"slow":60,"period_days":5}},
#            {"name":"bnf","params":{"ma":25,"threshold":0.12,"period_days":5}}],
#  "universe":["005930"], "start":"2023-01-02","end":"2023-12-28","cash":10000000}
import json

from AlgorithmImports import *

from krx_framework import KrxFrameworkAlgorithm
from alphas import build_alpha


class Composed(KrxFrameworkAlgorithm):
    def initialize(self):
        spec = json.loads(self.get_parameter("composition"))

        start = spec["start"].split("-")
        end = spec["end"].split("-")
        self.set_start_date(int(start[0]), int(start[1]), int(start[2]))
        self.set_end_date(int(end[0]), int(end[1]), int(end[2]))
        self.set_cash(int(spec.get("cash", 10_000_000)))

        self.setup_krx_framework()

        symbols = self.krx_symbols(spec["universe"])
        self.set_universe_selection(ManualUniverseSelectionModel(symbols))
        if symbols:
            self.set_benchmark(symbols[0])

        # 고른 Alpha들을 결합 (PortfolioConstruction이 종목당 1목표로 합산)
        for a in spec["alphas"]:
            self.add_alpha(build_alpha(a["name"], a.get("params", {})))
