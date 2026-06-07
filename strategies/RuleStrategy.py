# 규칙 기반 전략 — 사용자가 만든 불리언 식(예: "(EMA AND MACD) OR (RSI AND MOM)")을 실행.
#
# 설계(2-층, docs/ARCHITECTURE.md):
#  ① 선별 = 항상 '전날 데이터 1회'(장중 재선별 없음). 신호 지표는 Resolution.DAILY로 계산.
#  ② 체결 타이밍 = 시가/종가(일봉) 또는 특정시각/TWAP/눌림목(분봉). 선별과 분리.
#
# rule_spec 예:
# {"signals":{"EMA":{"type":"ema","params":{"fast":12,"slow":26}}},
#  "rule":"EMA AND MACD", "resolution":"daily"|"minute",
#  "execution":{"timing":"open"|"close"|"time"|"twap"|"pullback", ...},
#  "universe":["005930"],"start":"2023-01-02","end":"2023-12-28","cash":10000000}
import json

from AlgorithmImports import *

from orchestrator.rules import parse_rule, eval_rule, signal_labels, UP, DOWN
from orchestrator.execution import TimingConfig
from krx_framework import KrxFrameworkAlgorithm
from signals import build_signal
from intraday_execution import IntradayExecutionModel


class RuleAlpha(AlphaModel):
    """선별 알파 — 항상 '전날(완성 일봉) 기준'으로 하루 1회 종목을 고른다.

    분봉 구독이어도 선별 시점은 하루 첫 분봉 1회뿐(장중 재선별 없음). 가격계열 신호도 완성된
    일봉 지표(Resolution.DAILY)를 쓴다 → 장중 노이즈에 흔들리지 않음. 체결 타이밍은 ②층(실행모델).
    """

    def __init__(self, signals_config: dict, rule: str, period_days: int = 5,
                 max_positions: int = 0, minute_res: bool = False):
        self.signals_config = signals_config          # 라벨 -> {type, params}
        self.ast = parse_rule(rule)
        self.used = signal_labels(self.ast)            # 식에 실제 쓰인 라벨만 평가
        self.hold = timedelta(days=period_days)
        # 동시 보유 상한. 매수 신호가 자본 대비 너무 많으면 균등분할 시 종목당 배분이 1주 미만이
        # 되어 매매가 안 된다. 전체 종목 스캔은 유지하되, 매수 신호가 이보다 많으면 유동성 상위만 낸다.
        self.max_positions = max_positions
        self.minute_res = minute_res          # 분봉 구독이면 update가 매 분봉 호출됨(선별은 1회로 게이트)
        self._decided_day = None              # 당일 선별 완료 여부(하루 1회 보장)
        self._evals = {}                      # symbol -> {라벨: 평가기}

    def on_securities_changed(self, algorithm, changes):
        for sec in changes.added_securities:
            if sec.symbol.security_type != SecurityType.EQUITY:
                continue
            # 선별은 항상 완성 일봉 기준 → 가격계열 신호도 일봉 지표(intraday=False).
            self._evals[sec.symbol] = {
                label: build_signal(self.signals_config[label]["type"],
                                    self.signals_config[label].get("params", {}),
                                    algorithm, sec.symbol, intraday=False)
                for label in self.used}
        for sec in changes.removed_securities:
            self._evals.pop(sec.symbol, None)

    def update(self, algorithm, data):
        today = algorithm.time.date()
        # 분봉 구독이어도 선별은 하루 1회(첫 분봉). 이후 분봉은 기존 인사이트 유지(체결은 ②층).
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
            elif result == DOWN and algorithm.portfolio[symbol].invested:  # 보유 중일 때만 청산
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
        # 백테스트는 start/end/cash를 지정하지만, 라이브는 현재 시각부터·계좌 잔액 기준이라 없다.
        if spec.get("start") and spec.get("end"):
            s, e = spec["start"].split("-"), spec["end"].split("-")
            self.set_start_date(int(s[0]), int(s[1]), int(s[2]))
            self.set_end_date(int(e[0]), int(e[1]), int(e[2]))
        if spec.get("cash"):
            self.set_cash(int(spec["cash"]))

        ex = spec.get("execution", {}) or {}
        # 체결 타이밍이 해상도를 결정: 특정시각/TWAP/눌림목 → 분봉, 시가/종가 → 일봉.
        # (resolution 필드는 설정 모델이 타이밍에서 도출해 넣어줌. 방어적으로 재확인.)
        intraday = str(spec.get("resolution", "daily")).lower() == "minute"
        # 리스크 판단은 항상 완성된 일봉(종가) 1회 — 선별과 같은 철학. 분봉 체결이어도 장중 매분
        # 평가는 안 한다(노이즈에 손절·트레일링이 계속 발동→과매매). 분봉이면 DailyGated로 마감 1회.
        risk_eval_daily = ex.get("risk_eval", "daily") != "bar"
        self.setup_krx_framework(Resolution.MINUTE if intraday else Resolution.DAILY,
                                 risk_eval_daily=risk_eval_daily)

        symbols = self.krx_symbols(spec["universe"])
        self.set_universe_selection(ManualUniverseSelectionModel(symbols))
        if symbols:
            self.set_benchmark(symbols[0])

        if intraday:
            # ② 장중 체결(특정시각/TWAP/눌림목). style·at_min은 설정 모델이 타이밍에서 매핑.
            cfg = TimingConfig(
                style=ex.get("style", "twap"),
                entry_drop_pct=float(ex.get("entry_drop_pct", 1.0)),
                exit_rebound_pct=float(ex.get("exit_rebound_pct", 1.0)),
                slices=int(ex.get("slices", 6)),
                force_by_close=bool(ex.get("force_by_close", True)),
                at_min=int(ex.get("at_min", 0)),
            )
            # (종목,일)별 분봉 적재 여부 — 있으면 장중 타점, 없으면 시가 폴백.
            from etl.lean_format import list_minute_days
            data_dir = spec.get("data_folder", "./data")
            avail = {t: list_minute_days(data_dir, "krx", t) for t in spec["universe"]}
            self.set_execution(IntradayExecutionModel(cfg, available_days=avail))
        elif ex.get("daily_fill") == "close":
            # 일봉 종가 체결: 다음 거래일 MarketOnClose. ('open'은 프레임워크 기본=다음 시가 시장가)
            from daily_execution import DailyExecutionModel
            self.set_execution(DailyExecutionModel(fill="close"))

        self.add_alpha(RuleAlpha(spec["signals"], spec["rule"],
                                 int(spec.get("period_days", 5)),
                                 int(spec.get("max_positions", 0)),
                                 minute_res=intraday))
