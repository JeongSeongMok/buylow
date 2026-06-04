# 일봉 체결 실행모델 (얇은 LEAN 어댑터).
#
# 신호는 당일 일봉 '종가'에 계산되므로, 룩어헤드 방지로 체결은 항상 '다음 거래일'에 일어난다.
#  - fill='open'  : 시장가 주문 → 다음 거래일 시가 근처 체결(프레임워크 기본과 동일).
#  - fill='close' : MarketOnClose 주문 → 다음 거래일 '종가' 체결.
#
# 설계 메모(왜 OrderSizing): MarketOnClose는 비동기(제출 후 그날 종가에 체결)라, 같은 목표로
# 다음 분봉/일봉에서 또 제출하면 중복 주문이 난다. OrderSizing.get_unordered_quantity는
# 보유분뿐 아니라 '미체결 주문'까지 빼주므로, 체결 대기 중엔 추가 주문을 내지 않는다.
from AlgorithmImports import *


class DailyExecutionModel(ExecutionModel):
    def __init__(self, fill: str = "open"):
        self.fill = "close" if fill == "close" else "open"
        self._targets = PortfolioTargetCollection()

    def execute(self, algorithm, targets):
        self._targets.add_range(targets)
        for target in self._targets.order_by_margin_impact(algorithm):
            symbol = target.symbol
            # 미체결 주문까지 반영한 잔여 수량(중복 주문 방지 — 위 설계 메모 참고).
            qty = OrderSizing.get_unordered_quantity(algorithm, target)
            if qty == 0:
                continue
            if self.fill == "close":
                algorithm.market_on_close_order(symbol, qty, tag=target.tag)
            else:
                algorithm.market_order(symbol, qty, tag=target.tag)
        self._targets.clear_fulfilled(algorithm)
