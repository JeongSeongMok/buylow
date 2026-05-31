# KRX 전략 프레임워크 베이스 (LEAN Alpha 프레임워크).
#
# 멀티전략을 "독립 봇"이 아니라 **여러 AlphaModel(신호) → 하나의 포트폴리오**로 결합한다.
# 종목당 목표 비중은 PortfolioConstruction이 합산하므로, 한 전략이 산 걸 다른 전략이
# 임의로 파는 충돌이 없다(docs/ARCHITECTURE.md, "A 방식").
#
# 하위 클래스는 initialize에서: 날짜/현금 → setup_krx_framework() → 유니버스 + add_alpha(...).
from AlgorithmImports import *

from market.krx import KRX_MARKET, KRX_MARKET_ID, KRX_CURRENCY
from krx import KoreanFeeModel  # strategies/krx.py


class KrxFrameworkAlgorithm(QCAlgorithm):
    def setup_krx_framework(self, resolution=Resolution.DAILY):
        # krx 시장 등록 + KRW 계좌 (set_cash 전에 통화 지정)
        Market.add(KRX_MARKET, KRX_MARKET_ID)
        self.set_account_currency(KRX_CURRENCY)
        self.universe_settings.resolution = resolution
        # 유니버스가 편입하는 모든 종목에 한국 수수료모델 부착 (프레임워크에선 initializer로)
        self.set_security_initializer(lambda s: s.set_fee_model(KoreanFeeModel()))
        # 결합/실행 기본값: 동일비중(=활성 신호들을 종목당 1목표로 합산) + 즉시 체결
        self.set_portfolio_construction(EqualWeightingPortfolioConstructionModel())
        self.set_execution(ImmediateExecutionModel())
        self._apply_risk_management()
        self._setup_progress_logging()

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

    def _apply_risk_management(self):
        # 전역 리스크 설정(Runner가 risk_* 파라미터로 주입). %값이라 /100. 여러 개면 합성.
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
            self.set_risk_management(CompositeRiskManagementModel(*models))

    def krx_symbols(self, tickers: list[str]) -> list:
        # 수동 유니버스용 KRX 심볼 생성 (Market.add 이후 호출).
        return [Symbol.create(t, SecurityType.EQUITY, KRX_MARKET) for t in tickers]
