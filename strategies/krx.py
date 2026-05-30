# KRX(한국) 전략용 베이스 — LEAN 런타임(pythonnet) 안에서 동작.
#
# 한국 종목 전략이 매번 반복하는 설정(krx 시장 등록·KRW 계좌·한국 수수료모델 부착)을 모아둔다.
# 순수 로직(수수료 계산·상수)은 market/krx.py에서 가져온다(LEAN 없이 단위 테스트 가능하게 분리).
# Runner가 repo 루트를 PYTHONPATH에 넣어주므로 'from market.krx import ...'가 LEAN 안에서도 해소된다.
from AlgorithmImports import *

from market.krx import (
    KRX_MARKET, KRX_MARKET_ID, KRX_CURRENCY,
    korean_fee,
)


class KoreanFeeModel(FeeModel):
    """한국 수수료/거래세 모델. 매수=수수료, 매도=수수료+증권거래세 (market.krx.korean_fee)."""

    def get_order_fee(self, parameters):
        order = parameters.order
        security = parameters.security
        fee = korean_fee(float(security.price), float(order.quantity))
        return OrderFee(CashAmount(fee, KRX_CURRENCY))


class KrxAlgorithm(QCAlgorithm):
    """한국 종목 전략 베이스. 하위 클래스는 initialize에서 setup_krx() → 날짜/현금 → add_krx_equity 순으로 호출."""

    def setup_krx(self):
        # krx 시장을 런타임 등록(LEAN 기본 미지원). set_cash 전에 통화부터 지정해야 한다.
        Market.add(KRX_MARKET, KRX_MARKET_ID)
        self.set_account_currency(KRX_CURRENCY)

    def add_krx_equity(self, ticker: str, resolution=Resolution.DAILY):
        # add_equity + 한국 수수료모델 부착(기본 IB 수수료모델은 krx를 모름).
        equity = self.add_equity(ticker, resolution, KRX_MARKET)
        equity.set_fee_model(KoreanFeeModel())
        # 기본 벤치마크(미국 SPY) 의존을 피하려고 첫 종목을 벤치마크로.
        if self.benchmark is None or not getattr(self, "_krx_benchmark_set", False):
            self.set_benchmark(equity.symbol)
            self._krx_benchmark_set = True
        return equity
