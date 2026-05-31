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

    def krx_symbols(self, tickers: list[str]) -> list:
        # 수동 유니버스용 KRX 심볼 생성 (Market.add 이후 호출).
        return [Symbol.create(t, SecurityType.EQUITY, KRX_MARKET) for t in tickers]
