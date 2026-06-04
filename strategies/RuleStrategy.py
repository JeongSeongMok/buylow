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
                 max_positions: int = 0, minute_res: bool = False, select_eval: str = "close"):
        self.signals_config = signals_config          # 라벨 -> {type, params}
        self.ast = parse_rule(rule)
        self.used = signal_labels(self.ast)            # 식에 실제 쓰인 라벨만 평가
        self.hold = timedelta(days=period_days)
        # 동시 보유 상한. 매수 신호가 자본 대비 너무 많으면 균등분할 시 종목당 배분이 1주 미만이
        # 되어 매매가 안 된다. 전체 종목 스캔은 유지하되, 매수 신호가 이보다 많으면 유동성 상위만 낸다.
        self.max_positions = max_positions
        self.minute_res = minute_res          # 분봉 구독(update가 매 분봉 호출)
        # 선별 주기: 'intraday'면 매 분봉 진행 중 일봉(현재가) 포함 재평가, 아니면 전날 종가 1회.
        self.select_intraday = minute_res and select_eval == "intraday"
        self._decided_day = None
        self._day = None
        self._exited_today = {}               # symbol -> date (당일 청산 → 재진입 금지 쿨다운)
        self._evals = {}                       # symbol -> {라벨: 평가기}

    def on_securities_changed(self, algorithm, changes):
        for sec in changes.added_securities:
            if sec.symbol.security_type != SecurityType.EQUITY:
                continue
            evals = {}
            for label in self.used:
                cfg = self.signals_config[label]
                # 장중 선별이면 가격계열 신호는 잠정평가(현재가 포함) 모드로 생성.
                evals[label] = build_signal(cfg["type"], cfg.get("params", {}), algorithm,
                                            sec.symbol, intraday=self.select_intraday)
            self._evals[sec.symbol] = evals
        for sec in changes.removed_securities:
            self._evals.pop(sec.symbol, None)

    def update(self, algorithm, data):
        today = algorithm.time.date()
        if self.minute_res:
            if self.select_intraday:
                if today != self._day:        # 새 거래일: 쿨다운 초기화
                    self._day = today
                    self._exited_today.clear()
            else:
                # 전날 종가 1회 선별: 하루 첫 분봉만 평가(이후 분봉은 기존 인사이트 유지).
                if today == self._decided_day:
                    return []
                self._decided_day = today
        ups, exits, reason = [], [], {}
        for symbol, evals in self._evals.items():
            directions = {label: ev.direction() for label, ev in evals.items()}
            result = eval_rule(self.ast, directions)
            if result == UP:
                if self.select_intraday and self._exited_today.get(symbol) == today:
                    continue  # 당일 청산 종목은 재진입 금지(휩쏘 방지)
                ups.append(symbol)
                reason[symbol] = "+".join(l for l, d in directions.items() if d == UP)
            elif result == DOWN and algorithm.portfolio[symbol].invested:  # 보유 중일 때만 청산 신호
                exits.append(Insight.price(symbol, self.hold, InsightDirection.DOWN))
                if self.select_intraday:
                    self._exited_today[symbol] = today
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
        # 분봉인데 리스크는 '일별(종가)'로 평가 선택 시 게이트 적용(일봉은 어차피 일별).
        ex_spec = spec.get("execution", {}) or {}
        risk_eval_daily = intraday and ex_spec.get("risk_eval", "bar") == "daily"
        self.setup_krx_framework(Resolution.MINUTE if intraday else Resolution.DAILY,
                                 risk_eval_daily=risk_eval_daily)

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
        elif ex_spec.get("daily_fill") == "close":
            # 일봉 종가 체결: 다음 거래일 MarketOnClose. ('open'은 프레임워크 기본=다음 시가 시장가)
            from daily_execution import DailyExecutionModel
            self.set_execution(DailyExecutionModel(fill="close"))

        self.add_alpha(RuleAlpha(spec["signals"], spec["rule"],
                                 int(spec.get("period_days", 5)),
                                 int(spec.get("max_positions", 0)),
                                 minute_res=intraday,
                                 select_eval=ex_spec.get("select_eval", "close")))
