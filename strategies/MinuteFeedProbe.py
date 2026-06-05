# 분봉 데이터피드 진단용 최소 프로브 (전략/체결/유니버스모델 전부 제거).
#
# 목적: "분봉 백테스트가 ~1개월 후 가격을 동결하는" 현상이 전략 때문인지, 순수 LEAN 데이터피드
# 때문인지 분리한다. 이 알고리즘은 매매를 전혀 하지 않고, 2개 종목의 분봉을 구독해 '매일 몇 개의
# 분봉이 들어왔는지 + 마지막 가격'만 로그한다. 어느 날부터 분봉이 0개로 끊기는지가 곧 답이다.
#
# 해석:
#  - 2종목인데도 ~2025-07-18 부근에서 분봉이 0으로 끊기면 → 유니버스 규모와 무관한 LEAN 분봉
#    로딩 근본 문제(작은 재현으로 추가 추적 가능).
#  - 2종목에선 끝까지 정상 유입되면 → 348종목 동시구독 규모(캐시/자원) 문제로 좁혀진다.
import json
from datetime import datetime

from AlgorithmImports import *

from market.krx import KRX_MARKET, KRX_MARKET_ID, KRX_CURRENCY

# 풀 기간 분봉을 가진 것으로 검증된 종목(원하면 바꿔도 됨).
PROBE_TICKERS = ["000100", "005930"]
PROBE_START = (2025, 6, 5)
PROBE_END = (2025, 9, 30)   # 관측된 동결 시점(~07-18) + 약 2개월
REF = "000100"              # 일별 상세를 찍을 기준 종목


class MinuteFeedProbe(QCAlgorithm):
    def initialize(self):
        self.set_start_date(*PROBE_START)
        self.set_end_date(*PROBE_END)
        self.set_time_zone("Asia/Seoul")
        self.set_risk_free_interest_rate_model(ConstantRiskFreeRateInterestRateModel(0.03))
        Market.add(KRX_MARKET, KRX_MARKET_ID)
        self.set_account_currency(KRX_CURRENCY)
        self.set_cash(10_000_000)
        self.universe_settings.resolution = Resolution.MINUTE

        # 유니버스: tickers_file(JSON 배열) 파라미터가 있으면 그걸로(규모 테스트), 없으면 기본 2종목.
        tickers = PROBE_TICKERS
        tf = self.get_parameter("tickers_file")
        if tf:
            try:
                with open(tf, encoding="utf-8") as f:
                    tickers = [str(t).strip() for t in json.load(f) if str(t).strip()]
            except Exception as e:
                self.log(f"PROBE tickers_file 로드 실패({type(e).__name__}) → 기본 2종목")

        self.syms = []
        for t in tickers:
            eq = self.add_equity(t, Resolution.MINUTE, KRX_MARKET)
            self.syms.append(eq.symbol)
        self.log(f"PROBE universe={len(self.syms)}종목")
        if self.syms:
            self.set_benchmark(self.syms[0])  # SPY(us) 폴백 방지

        self._ref = next((s for s in self.syms if s.value == REF), self.syms[0] if self.syms else None)
        self._day = None
        self._bars = {s: 0 for s in self.syms}
        self._last = {s: 0.0 for s in self.syms}

    def _flush_day(self):
        if self._day is None:
            return
        active = sum(1 for s in self.syms if self._bars[s] > 0)   # 오늘 분봉이 1개라도 온 종목 수
        ref = self._ref
        refinfo = f"{ref.value}=bars:{self._bars.get(ref,0)},last:{self._last.get(ref,0):.0f}" if ref else "-"
        # PROBE_DAY <날짜> active=<유입종목>/<전체> | <기준종목 상세>
        self.log(f"PROBE_DAY {self._day} active={active}/{len(self.syms)} | {refinfo}")
        for s in self.syms:
            self._bars[s] = 0

    def on_data(self, data):
        d = self.time.date()
        if self._day is not None and d != self._day:
            self._flush_day()
        self._day = d
        for s in self.syms:
            if s in data.bars:
                self._bars[s] += 1
                self._last[s] = float(data.bars[s].close)

    def on_end_of_algorithm(self):
        self._flush_day()
        ref = self._ref
        if ref is None:
            return
        try:
            for label, d0, d1 in [("pre", datetime(2025, 7, 10), datetime(2025, 7, 11)),
                                  ("post", datetime(2025, 9, 1), datetime(2025, 9, 2))]:
                h = self.history(ref, d0, d1, Resolution.MINUTE)
                self.log(f"PROBE_HISTORY {label} {ref.value} {d0:%Y-%m-%d}: rows={len(h)}")
        except Exception as e:
            self.log(f"PROBE_HISTORY error: {type(e).__name__} {e}")
