# 장중 체결 타이밍 실행모델 (②층의 얇은 LEAN 어댑터).
#
# 일봉 선별(RuleAlpha)이 낸 목표(PortfolioTarget)를, 분봉마다 호출되는 이 모델이 "장중 언제/얼마나"
# 체결할지 결정한다. 결정 로직 자체는 LEAN 비의존 순수 모듈(orchestrator/execution.py)에 있고
# (그래서 단위테스트 가능), 이 클래스는 LEAN 상태(보유/시각/주문)를 거기에 연결하는 얇은 글루다.
# 백테스트(과거 분봉)와 라이브(실시간 분봉)에서 같은 코드가 돈다 — 정합성.
#
# 설계 메모(왜):
#  - 기준가(reference)는 '당일 첫 분봉 가격'(≈시초가)으로 잡는다. 눌림목/반등은 시초가 대비로 판단.
#  - total_delta(오늘 채울 총 변화량)는 당일 첫 결정 시 고정 → TWAP 누적 스케줄의 분모.
#  - 분봉에선 시장가를 동기 체결(asynchronous=False)해 'filled = total_delta - remaining' 회계를 단순화.
from AlgorithmImports import *

from orchestrator.execution import (
    TimingConfig, decide_submit, minutes_since_open, is_last_bar,
)


class IntradayExecutionModel(ExecutionModel):
    def __init__(self, cfg: TimingConfig, available_days: dict | None = None):
        self.cfg = cfg.normalized()
        # {티커: {분봉 적재된 date}} — 그 (종목,일)에 분봉이 있으면 장중 타점, 없으면 시가 폴백.
        self.available_days = available_days or {}
        self._targets = PortfolioTargetCollection()
        self._state = {}  # symbol -> {"day": date, "ref": float, "total": int, "cfg": TimingConfig}

    def execute(self, algorithm, targets):
        self._targets.add_range(targets)
        if self._targets.is_empty:
            return

        t = algorithm.time
        day = t.date()
        elapsed = minutes_since_open(t.hour, t.minute)
        last = is_last_bar(t.hour, t.minute)

        for target in self._targets.order_by_margin_impact(algorithm):
            symbol = target.symbol
            security = algorithm.securities[symbol]
            price = float(security.price)
            if price <= 0:
                continue

            # 미체결 잔량(부호有) = 목표보유 − 현재보유. 동기 체결이라 미체결주문 누적은 없다.
            remaining = int(target.quantity - security.holdings.quantity)

            st = self._state.get(symbol)
            if st is None or st["day"] != day:
                # 새 거래일: 기준가=당일 첫 가격(≈시초가), total_delta=오늘 채울 총량 고정.
                # 그 (종목,일)에 분봉이 있으면 설정 스타일, 없으면 시가 즉시 폴백.
                avail = day in self.available_days.get(symbol.value, ())
                st = {"day": day, "ref": price, "total": remaining,
                      "cfg": self.cfg.for_availability(avail)}
                self._state[symbol] = st
            elif st["total"] == 0 and remaining != 0:
                # 장중 새 목표가 들어온 드문 경우 — total_delta 갱신.
                st["total"] = remaining

            qty = decide_submit(
                st["cfg"],
                remaining=remaining,
                total_delta=int(st["total"]),
                current_price=price,
                reference_price=float(st["ref"]),
                elapsed_min=elapsed,
                last_bar=last,
            )
            if qty != 0:
                algorithm.market_order(security, qty, asynchronous=False, tag=target.tag)

        self._targets.clear_fulfilled(algorithm)

    def on_securities_changed(self, algorithm, changes):
        for sec in changes.removed_securities:
            self._state.pop(sec.symbol, None)
