# 규칙 기반 전략 — 사용자가 만든 불리언 식(예: "(EMA AND MACD) OR (RSI AND MOM)")을 실행.
#
# 'rule_spec' 파라미터(JSON)를 읽어 signal들을 만들고, 매 시점 종목마다 각 signal의 방향을
# 평가한 뒤 식을 평가해 최종 방향(UP/DOWN/NONE)으로 Insight를 낸다.
#
# rule_spec 예:
# {"signals":{"EMA":{"type":"ema","params":{"fast":12,"slow":26}},
#             "MACD":{"type":"macd","params":{"fast":12,"slow":26,"signal":9}}},
#  "rule":"EMA AND MACD",
#  "universe":["005930"],"start":"2023-01-02","end":"2023-12-28","cash":10000000}
import json

from AlgorithmImports import *

from orchestrator.rules import parse_rule, eval_rule, signal_labels, UP, DOWN
from krx_framework import KrxFrameworkAlgorithm
from signals import build_signal


class RuleAlpha(AlphaModel):
    def __init__(self, signals_config: dict, rule: str, period_days: int = 5):
        self.signals_config = signals_config          # 라벨 -> {type, params}
        self.ast = parse_rule(rule)
        self.used = signal_labels(self.ast)            # 식에 실제 쓰인 라벨만 평가
        self.hold = timedelta(days=period_days)
        self._evals = {}                               # symbol -> {라벨: 평가기}

    def on_securities_changed(self, algorithm, changes):
        for sec in changes.added_securities:
            if sec.symbol.security_type != SecurityType.EQUITY:
                continue
            evals = {}
            for label in self.used:
                cfg = self.signals_config[label]
                evals[label] = build_signal(cfg["type"], cfg.get("params", {}), algorithm, sec.symbol)
            self._evals[sec.symbol] = evals
        for sec in changes.removed_securities:
            self._evals.pop(sec.symbol, None)

    def update(self, algorithm, data):
        insights = []
        for symbol, evals in self._evals.items():
            directions = {label: ev.direction() for label, ev in evals.items()}
            result = eval_rule(self.ast, directions)
            if result == UP:
                insights.append(Insight.price(symbol, self.hold, InsightDirection.UP))
            elif result == DOWN:
                insights.append(Insight.price(symbol, self.hold, InsightDirection.DOWN))
        return insights


class RuleStrategy(KrxFrameworkAlgorithm):
    def initialize(self):
        spec = json.loads(self.get_parameter("rule_spec"))
        s, e = spec["start"].split("-"), spec["end"].split("-")
        self.set_start_date(int(s[0]), int(s[1]), int(s[2]))
        self.set_end_date(int(e[0]), int(e[1]), int(e[2]))
        self.set_cash(int(spec.get("cash", 10_000_000)))

        self.setup_krx_framework()

        symbols = self.krx_symbols(spec["universe"])
        self.set_universe_selection(ManualUniverseSelectionModel(symbols))
        if symbols:
            self.set_benchmark(symbols[0])

        self.add_alpha(RuleAlpha(spec["signals"], spec["rule"],
                                 int(spec.get("period_days", 5))))
