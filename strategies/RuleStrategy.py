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
from orchestrator.execution import TimingConfig
from krx_framework import KrxFrameworkAlgorithm
from signals import build_signal
from intraday_execution import IntradayExecutionModel


class RuleAlpha(AlphaModel):
    def __init__(self, signals_config: dict, rule: str, period_days: int = 5,
                 max_positions: int = 0, intraday: bool = False):
        self.signals_config = signals_config          # 라벨 -> {type, params}
        self.ast = parse_rule(rule)
        self.used = signal_labels(self.ast)            # 식에 실제 쓰인 라벨만 평가
        self.hold = timedelta(days=period_days)
        # 동시 보유 상한. 매수 신호가 자본 대비 너무 많으면 균등분할 시 종목당 배분이 1주 미만이
        # 되어 매매가 안 된다. 전체 종목 스캔은 유지하되, 매수 신호가 이보다 많으면 유동성 상위만 낸다.
        self.max_positions = max_positions
        # 분봉 실행 모드: update()가 매 분봉 호출되므로, 선별은 '거래일 1회'로 제한한다.
        # (지표는 Resolution.DAILY라 전날 종가까지만 반영 → 장중 실행은 ExecutionModel 몫.)
        self.intraday = intraday
        self._decided_day = None
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
        # 분봉 모드: 하루 첫 평가만 선별을 내고, 이후 분봉에선 빈 리스트(기존 인사이트는 유지).
        # 장중의 '언제 살지/팔지'는 IntradayExecutionModel이 담당한다.
        if self.intraday:
            today = algorithm.time.date()
            if today == self._decided_day:
                return []
            self._decided_day = today
        ups, exits, reason = [], [], {}
        for symbol, evals in self._evals.items():
            directions = {label: ev.direction() for label, ev in evals.items()}
            result = eval_rule(self.ast, directions)
            if result == UP:
                ups.append(symbol)
                reason[symbol] = "+".join(l for l, d in directions.items() if d == UP)
            elif result == DOWN and algorithm.portfolio[symbol].invested:  # 보유 중일 때만 청산 신호
                exits.append(Insight.price(symbol, self.hold, InsightDirection.DOWN))
                self._log_hit(algorithm, symbol, "SELL",
                              "+".join(l for l, d in directions.items() if d == DOWN))
        # 매수 신호가 상한보다 많으면 유동성(당일 거래대금=가격×거래량) 상위만 보유.
        if self.max_positions and len(ups) > self.max_positions:
            ups.sort(key=lambda s: self._liquidity(algorithm, s), reverse=True)
            ups = ups[:self.max_positions]
        for s in ups:
            self._log_hit(algorithm, s, "BUY", reason.get(s, ""))
        return [Insight.price(s, self.hold, InsightDirection.UP) for s in ups] + exits

    def _log_hit(self, algorithm, symbol, side, labels):
        # 트리거 사유를 로그로 남겨 결과 페이지(거래 내역)가 파싱해 표시한다.
        # 형식: RULEHIT YYYY-MM-DD <종목> BUY|SELL <발동시그널들>
        algorithm.log(f"RULEHIT {algorithm.time:%Y-%m-%d} {symbol.value} {side} {labels}")

    def _liquidity(self, algorithm, symbol):
        # 당일 거래대금 = 종가 × 거래량 (시장에서 실제 체결 가능한 규모의 척도).
        sec = algorithm.securities[symbol]
        return float(sec.price) * float(sec.volume)


class RuleStrategy(KrxFrameworkAlgorithm):
    def initialize(self):
        spec = json.loads(self.get_parameter("rule_spec"))
        s, e = spec["start"].split("-"), spec["end"].split("-")
        self.set_start_date(int(s[0]), int(s[1]), int(s[2]))
        self.set_end_date(int(e[0]), int(e[1]), int(e[2]))
        self.set_cash(int(spec.get("cash", 10_000_000)))

        # 해상도: 'minute'면 일봉 선별 + 장중(분봉) 타이밍 실행, 그 외(기본)는 일봉/다음시가 체결.
        # 신호 지표는 Resolution.DAILY로 생성되므로, 분봉 구독에서도 선별은 일봉 기준 유지된다.
        intraday = str(spec.get("resolution", "daily")).lower() == "minute"
        self.setup_krx_framework(Resolution.MINUTE if intraday else Resolution.DAILY)

        symbols = self.krx_symbols(spec["universe"])
        self.set_universe_selection(ManualUniverseSelectionModel(symbols))
        if symbols:
            self.set_benchmark(symbols[0])

        if intraday:
            # 장중 타이밍 실행모델로 교체(기본 ImmediateExecutionModel 대신). 파라미터는 스펙의 execution.
            ex = spec.get("execution", {}) or {}
            cfg = TimingConfig(
                style=ex.get("style", "pullback"),
                entry_drop_pct=float(ex.get("entry_drop_pct", 1.0)),
                exit_rebound_pct=float(ex.get("exit_rebound_pct", 1.0)),
                slices=int(ex.get("slices", 6)),
                force_by_close=bool(ex.get("force_by_close", True)),
            )
            # (종목,일)별 분봉 적재 여부 맵 — 있으면 장중 타점, 없으면 시가 폴백.
            from etl.lean_format import list_minute_days
            data_dir = spec.get("data_folder", "./data")
            avail = {t: list_minute_days(data_dir, "krx", t) for t in spec["universe"]}
            self.set_execution(IntradayExecutionModel(cfg, available_days=avail))

        self.add_alpha(RuleAlpha(spec["signals"], spec["rule"],
                                 int(spec.get("period_days", 5)),
                                 int(spec.get("max_positions", 0)),
                                 intraday=intraday))
