"""장중 체결 타이밍 결정 — 순수 로직(LEAN 비의존, 단위테스트 가능).

두 층 구조의 ②층이다(docs/ARCHITECTURE.md):
  ① 일봉 선별(알파) — '무엇을/방향'(매수대상/청산대상)을 하루 1회 정함
  ② 장중 타이밍(이 모듈) — 그 대상을 '장중 언제/얼마나' 체결할지 정함

LEAN의 IntradayExecutionModel(얇은 어댑터)이 분봉마다 이 함수를 호출한다. 백테스트(과거 분봉)와
라이브(실시간 분봉)에서 동일 로직을 쓰므로 정합성이 보장된다 — 이 모듈엔 LEAN 의존이 없어
순수 단위테스트로 동작을 못박을 수 있다(rules.py와 같은 설계).

부호 규약: 수량은 LEAN과 동일하게 부호 있는 정수(매수 +, 청산/매도 −). remaining/total_delta는
'목표 - 보유'(미체결 잔량)이며, 반환값은 이번 분봉에 낼 주문 수량(같은 부호, |값| ≤ |remaining|).
"""

from __future__ import annotations

from dataclasses import dataclass

# 체결 스타일
IMMEDIATE = "immediate"  # 즉시 전량 (시초가 우르르 — 비교 기준선)
PULLBACK = "pullback"    # 눌림목 대기 진입 / 반등 청산 (기본)
TWAP = "twap"            # 시간 분할 (VWAP/TWAP 분할; 현재 시간가중, 거래량가중은 향후 훅)

STYLES = (IMMEDIATE, PULLBACK, TWAP)

# KRX 정규장(분 단위, 자정 기준). 09:00~15:30 = 390분.
KRX_OPEN_MIN = 9 * 60          # 540
KRX_CLOSE_MIN = 15 * 60 + 30   # 930
SESSION_MIN = KRX_CLOSE_MIN - KRX_OPEN_MIN  # 390


@dataclass
class TimingConfig:
    """장중 타이밍 파라미터. 대시보드/스펙에서 주입된다."""

    style: str = PULLBACK
    entry_drop_pct: float = 1.0     # 매수: 기준가(당일 시초가) 대비 N% 눌리면 진입
    exit_rebound_pct: float = 1.0   # 청산: 기준가 대비 N% 반등하면 매도
    slices: int = 6                 # TWAP 분할 수(정규장을 N등분)
    force_by_close: bool = True     # 트리거 미발생 시에도 장 마감 분봉에 잔량 전량 체결(미체결 방지)

    def normalized(self) -> "TimingConfig":
        s = self.style if self.style in STYLES else PULLBACK
        return TimingConfig(s, float(self.entry_drop_pct), float(self.exit_rebound_pct),
                            max(1, int(self.slices)), bool(self.force_by_close))


def minutes_since_open(hh: int, mm: int) -> int:
    """정규장 시작(09:00) 이후 경과 분. 장전이면 0, 장후면 SESSION_MIN으로 클램프."""
    return max(0, min(SESSION_MIN, hh * 60 + mm - KRX_OPEN_MIN))


def is_last_bar(hh: int, mm: int) -> bool:
    """정규장 마지막 분봉(>=15:29)인지 — force_by_close 잔량 체결 판단용."""
    return hh * 60 + mm >= KRX_CLOSE_MIN - 1


def slice_index(elapsed_min: int, slices: int) -> int:
    """경과 분 → 0-기반 분할 인덱스(0..slices-1). TWAP 누적 스케줄 계산용."""
    slices = max(1, int(slices))
    idx = int(elapsed_min * slices / max(1, SESSION_MIN))
    return max(0, min(idx, slices - 1))


def _twap_scheduled(total_delta: int, sidx: int, slices: int) -> int:
    """분할 인덱스 sidx까지 누적으로 체결돼 있어야 할 수량(부호 보존)."""
    return int(round(total_delta * (sidx + 1) / max(1, slices)))


def decide_submit(
    cfg: TimingConfig,
    *,
    remaining: int,
    total_delta: int,
    current_price: float,
    reference_price: float,
    elapsed_min: int,
    last_bar: bool,
) -> int:
    """이번 분봉에 낼 주문 수량(부호 있는 정수)을 결정한다.

    remaining: 이번 분봉 시점 미체결 잔량(목표−보유−미체결주문, 부호有)
    total_delta: 오늘 이 종목에 채워야 할 총 변화량(당일 첫 결정 시 고정, 부호有)
    current_price/reference_price: 현재가 / 당일 기준가(시초가)
    elapsed_min: 정규장 경과 분, last_bar: 마지막 분봉 여부
    """
    cfg = cfg.normalized()
    if remaining == 0:
        return 0

    # 마지막 분봉이면 스타일 불문 잔량 전량(force_by_close) — 미체결 방지.
    if last_bar and cfg.force_by_close:
        return remaining

    if cfg.style == IMMEDIATE:
        return remaining

    if cfg.style == PULLBACK:
        if current_price <= 0 or reference_price <= 0:
            return 0
        if remaining > 0:  # 매수: 기준가 대비 충분히 눌렸을 때
            triggered = current_price <= reference_price * (1 - cfg.entry_drop_pct / 100.0)
        else:              # 청산: 기준가 대비 충분히 반등했을 때
            triggered = current_price >= reference_price * (1 + cfg.exit_rebound_pct / 100.0)
        return remaining if triggered else 0

    if cfg.style == TWAP:
        if total_delta == 0:
            return 0
        filled = total_delta - remaining  # 지금까지 체결된 변화량(부호 동일)
        sidx = slice_index(elapsed_min, cfg.slices)
        want = _twap_scheduled(total_delta, sidx, cfg.slices) - filled
        # 부호 보존: 스케줄이 음(매도)이면 음수만, 양(매수)이면 양수만, 잔량 초과 금지.
        if total_delta > 0:
            return max(0, min(want, remaining))
        return min(0, max(want, remaining))

    return 0  # 알 수 없는 스타일 — 안전하게 미체결
