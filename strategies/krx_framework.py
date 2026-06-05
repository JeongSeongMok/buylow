# KRX 전략 프레임워크 베이스 (LEAN Alpha 프레임워크).
#
# 멀티전략을 "독립 봇"이 아니라 **여러 AlphaModel(신호) → 하나의 포트폴리오**로 결합한다.
# 종목당 목표 비중은 PortfolioConstruction이 합산하므로, 한 전략이 산 걸 다른 전략이
# 임의로 파는 충돌이 없다(docs/ARCHITECTURE.md, "A 방식").
#
# 하위 클래스는 initialize에서: 날짜/현금 → setup_krx_framework() → 유니버스 + add_alpha(...).
import json

from AlgorithmImports import *

from market.krx import KRX_MARKET, KRX_MARKET_ID, KRX_CURRENCY
from krx import KoreanFeeModel  # strategies/krx.py
from orchestrator.execution import should_daily_gate_eval


class DailyGatedRiskModel(RiskManagementModel):
    """분봉 구독에서도 리스크를 '일별(장 마감)'로만 평가하도록 내부 모델 호출을 게이트.

    분봉이면 리스크 모델이 매 분봉 호출돼 장중 변동에 손절/익절이 휘둘린다. 사용자가 '일별' 평가를
    고르면 이 래퍼가 마감 분봉에 하루 1회만 내부 모델에 위임해 일봉과 같은 종가 기준 평가로 만든다.
    """

    def __init__(self, inner):
        self.inner = inner
        self._last_day = None

    def manage_risk(self, algorithm, targets):
        t = algorithm.time
        if not should_daily_gate_eval(t.hour, t.minute, t.date(), self._last_day):
            return []
        self._last_day = t.date()
        return self.inner.manage_risk(algorithm, targets)

    def on_securities_changed(self, algorithm, changes):
        self.inner.on_securities_changed(algorithm, changes)


class LongOnlyEqualWeighting(EqualWeightingPortfolioConstructionModel):
    """롱온리 동일비중. UP 인사이트에만 1/N 비중을 주고 그 외(DOWN/FLAT)는 0으로 청산.

    기본 EqualWeighting은 DOWN 인사이트에 음수 비중(=공매도)을 줘서, KRX 백테스트가 공매도까지
    하게 된다. 한국 개인 계좌는 일반적으로 공매도가 안 되므로 음수 비중을 막아 롱온리로 강제한다.
    """

    def determine_target_percent(self, activeInsights):
        ups = [i for i in activeInsights if i.direction == InsightDirection.UP]
        n = len(ups)
        return {i: (1.0 / n if (n and i.direction == InsightDirection.UP) else 0.0)
                for i in activeInsights}


class KrxFrameworkAlgorithm(QCAlgorithm):
    def setup_krx_framework(self, resolution=Resolution.DAILY, risk_eval_daily=False):
        # 거래소 시간대를 한국으로(기본은 뉴욕 → 통계 왜곡 경고 + 벤치마크가 SPY로 폴백돼 깨짐).
        self.set_time_zone("Asia/Seoul")
        # 무위험금리: LEAN 기본은 미국 금리 CSV(data/alternative/interest-rate/usa)를 찾다 실패.
        # 한국 국고채 근사로 상수(3%) 모델을 써서 미국 데이터 의존을 제거(Sharpe 등 정상 계산).
        self.set_risk_free_interest_rate_model(ConstantRiskFreeRateInterestRateModel(0.03))
        # krx 시장 등록 + KRW 계좌 (set_cash 전에 통화 지정)
        Market.add(KRX_MARKET, KRX_MARKET_ID)
        self.set_account_currency(KRX_CURRENCY)
        self.universe_settings.resolution = resolution
        # 유니버스가 편입하는 모든 종목에 한국 수수료모델 부착 (프레임워크에선 initializer로)
        self.set_security_initializer(lambda s: s.set_fee_model(KoreanFeeModel()))
        # 결합/실행 기본값: 롱온리 동일비중(공매도 차단) + 즉시 체결
        self.set_portfolio_construction(LongOnlyEqualWeighting())
        self.set_execution(ImmediateExecutionModel())
        self._apply_risk_management(risk_eval_daily)
        self._setup_progress_logging()
        self._setup_fill_log()

    def _setup_fill_log(self):
        # 모든 체결을 우리가 직접 파일(fills.jsonl)에 남긴다. LEAN 결과 파일의 orders는 대량
        # 백테스트에서 0~100건만 직렬화돼(truncation) 거래내역이 비어 보이므로, on_order_event로
        # 완전한 체결 기록을 확보한다. 경로는 Runner가 trade_log 파라미터로 주입(run_dir/fills.jsonl).
        self._fill_log = None
        path = self.get_parameter("trade_log")
        if not path:
            return
        try:
            self._fill_log = open(path, "w", encoding="utf-8", buffering=1)
        except OSError:
            self._fill_log = None

    def on_order_event(self, order_event):
        # 체결(부분 포함)마다 한 줄씩 기록 — 대시보드가 그대로 파싱하도록 LEAN order dict 형태로 쓴다
        # (status=3, quantity=부호 있는 체결수량, price=체결가, value=체결금액, tag=주문 태그).
        if self._fill_log is None or order_event.fill_quantity == 0:
            return
        try:
            tag = ""
            order = self.transactions.get_order_by_id(order_event.order_id)
            if order is not None:
                tag = order.tag or ""
            qty = float(order_event.fill_quantity)
            price = float(order_event.fill_price)
            rec = {
                "status": 3,
                "quantity": qty,
                "tag": tag,
                "lastFillTime": self.time.strftime("%Y-%m-%dT%H:%M:%S"),
                "symbol": {"value": order_event.symbol.value},
                "price": price,
                "value": abs(price * qty),
            }
            self._fill_log.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except Exception:
            pass  # 체결 기록 실패가 백테스트를 깨지 않게

    def _setup_progress_logging(self):
        # 백테스트 진행률을 주 1회 로그로 남겨 대시보드 작업 화면이 파싱해 표시한다.
        # (현재 시뮬레이션 날짜 ÷ 전체 기간. 스케줄 불가 환경에서도 전략엔 영향 없게 감싼다.)
        try:
            self.schedule.on(self.date_rules.week_start(), self.time_rules.midnight,
                             self._emit_progress)
        except Exception:
            pass

    def _emit_progress(self):
        total = max((self.end_date - self.start_date).days, 1)
        done = (self.time - self.start_date).days
        pct = max(0.0, min(100.0, done / total * 100.0))
        self.debug(f"PROGRESS {pct:.0f}% {self.time:%Y-%m-%d}")

    def _apply_risk_management(self, risk_eval_daily=False):
        # 전역 리스크 설정(Runner가 risk_* 파라미터로 주입). %값이라 /100. 여러 개면 합성.
        # risk_eval_daily=True면(분봉인데 일별 평가 선택) 마감 1회만 평가하도록 게이트로 감싼다.
        def pct(name):
            v = self.get_parameter(name)
            try:
                return float(v) / 100.0
            except (TypeError, ValueError):
                return None
        sl, tp = pct("risk_stop_loss"), pct("risk_take_profit")
        tr = pct("risk_trailing")
        models = []
        if sl:
            models.append(MaximumDrawdownPercentPerSecurity(sl))          # 종목 손절
        if tp:
            models.append(MaximumUnrealizedProfitPercentPerSecurity(tp))  # 종목 익절
        if tr:
            models.append(TrailingStopRiskManagementModel(tr))            # 트레일링 스탑
        if models:
            composite = CompositeRiskManagementModel(*models)
            self.set_risk_management(DailyGatedRiskModel(composite) if risk_eval_daily else composite)

    def krx_symbols(self, tickers: list[str]) -> list:
        # 수동 유니버스용 KRX 심볼 생성 (Market.add 이후 호출).
        return [Symbol.create(t, SecurityType.EQUITY, KRX_MARKET) for t in tickers]
