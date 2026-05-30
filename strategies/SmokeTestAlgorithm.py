# LEAN 연동 스모크 테스트용 최소 전략.
#
# [목적] 토스/실거래 없이 "thin 런처 + 무수정 LEAN 엔진 + pythonnet(Python 3.11)"이
#        백테스트를 데이터피드→주문→통계까지 끝까지 도는지 확인하는 것.
#        매매 알고리즘이 아니라 "연동이 살아있는지"를 보는 헬스체크다.
#
# [왜 단순 보유인가] 연동 검증이 목적이라 매매 판단 로직은 일부러 넣지 않는다.
#        SPY를 100% 매수 후 보유하여 주문 1건 + 통계 산출만 확인한다.
#
# 데이터: LEAN 레퍼런스의 US equity daily 샘플(SPY)이 존재하는 구간으로 날짜 고정.
#        한국 시장/데이터 연동은 이후 단계(CLAUDE.md §4-1,2)에서 별도 검증한다.
from AlgorithmImports import *


class SmokeTestAlgorithm(QCAlgorithm):
    def initialize(self):
        self.set_start_date(2013, 10, 7)
        self.set_end_date(2013, 10, 11)
        self.set_cash(100000)
        self.spy = self.add_equity("SPY", Resolution.DAILY).symbol

    def on_data(self, data: Slice):
        # 미보유 상태면 전액 SPY 매수 → 이후 계속 보유
        if not self.portfolio.invested:
            self.set_holdings(self.spy, 1.0)
            self.debug(f"{self.time}: SPY 진입 (스모크 테스트)")
