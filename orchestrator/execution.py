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
IMMEDIATE = "immediate"  # 즉시 전량 (시초가 우르르 — 비교 기준선·폴백)
PULLBACK = "pullback"    # 눌림목 대기 진입 / 반등 청산
TWAP = "twap"            # 시간 분할(시간가중평균가). 거래량가중(VWAP)은 미구현 — 현 규모에선 TWAP로 충분
TIME = "time"            # 특정 시각(예 13:00)에 전량 체결

STYLES = (IMMEDIATE, PULLBACK, TWAP, TIME)

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
    at_min: int = 0                 # TIME 스타일 체결 시각(자정기준 분, 예 13:00=780)

    def normalized(self) -> "TimingConfig":
        s = self.style if self.style in STYLES else PULLBACK
        return TimingConfig(s, float(self.entry_drop_pct), float(self.exit_rebound_pct),
                            max(1, int(self.slices)), bool(self.force_by_close), int(self.at_min))

    def for_availability(self, available: bool) -> "TimingConfig":
        """그 (종목,일)에 분봉이 없으면 장중 타점이 불가하므로 '시가 즉시(IMMEDIATE)'로 폴백.

        분봉이 있으면 설정 스타일 그대로. 마감 강제체결 옵션은 보존한다.
        """
        if available:
            return self.normalized()
        n = self.normalized()
        return TimingConfig(IMMEDIATE, n.entry_drop_pct, n.exit_rebound_pct,
                            n.slices, n.force_by_close, n.at_min)


def minutes_since_open(hh: int, mm: int) -> int:
    """정규장 시작(09:00) 이후 경과 분. 장전이면 0, 장후면 SESSION_MIN으로 클램프."""
    return max(0, min(SESSION_MIN, hh * 60 + mm - KRX_OPEN_MIN))


def is_last_bar(hh: int, mm: int) -> bool:
    """정규장 마지막 분봉(>=15:29)인지 — force_by_close 잔량 체결 판단용."""
    return hh * 60 + mm >= KRX_CLOSE_MIN - 1


def should_daily_gate_eval(hh: int, mm: int, current_day, last_eval_day) -> bool:
    """분봉 구독에서 리스크를 '일별'로 평가할 때의 게이트: 장 마감 분봉에 하루 1회만 True.

    분봉이면 리스크 모델이 매 분봉 호출되는데, 이걸 통과시키면 일봉처럼 종가(마감) 기준
    하루 1회만 평가하게 된다(장중 흔들림에 손절이 휘둘리지 않게).
    """
    return is_last_bar(hh, mm) and current_day != last_eval_day


# ── 장중 선별(판단) 주기 ──────────────────────────────────────────────────
# 분봉 해상도는 기본 매 분봉 재선별이라 과매매·수수료가 커진다. 선별 주기를 데이터 해상도와
# 분리해(데이터는 분봉 유지), 'every'(매분)/'interval'(N분)/'times'(특정시각)로 게이트한다.
# 리스크 평가(손절·익절)는 이 게이트와 무관하게 매 분봉 유지한다(급락 대응 안전).
SELECT_EVERY = "every"        # 매 분봉(현행)
SELECT_INTERVAL = "interval"  # N분 간격
SELECT_TIMES = "times"        # 지정 시각 목록
SELECT_CADENCES = (SELECT_EVERY, SELECT_INTERVAL, SELECT_TIMES)


def parse_eval_times(times) -> tuple[int, ...]:
    """['09:30','10:05'] → 자정 기준 분(minute-of-day) 정렬 튜플. 잘못된 항목은 무시."""
    out: list[int] = []
    for t in times or []:
        try:
            hh, mm = str(t).strip().split(":")
            v = int(hh) * 60 + int(mm)
        except (ValueError, AttributeError):
            continue
        if 0 <= v < 24 * 60 and v not in out:
            out.append(v)
    return tuple(sorted(out))


def due_by_interval(elapsed_min: int, interval_min: int, last_eval_min) -> bool:
    """N분 간격 선별 게이트: 당일 첫 평가(last_eval_min is None)거나, 직전 평가 후
    interval_min 이상 경과했으면 True. (분봉 타임스탬프가 ±1 흔들려도 빠지지 않게 modulo 대신 누적차)"""
    return last_eval_min is None or (elapsed_min - int(last_eval_min)) >= max(1, int(interval_min))


def due_by_times(abs_min: int, eval_times_min, fired) -> bool:
    """특정시각 선별 게이트: 현재 분(자정기준)이 지정 시각이고 당일 아직 안 쐈으면 True."""
    return abs_min in eval_times_min and abs_min not in fired


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

    if cfg.style == TIME:
        # 목표 시각(at_min)에 도달하면 전량 체결, 그 전엔 대기. (그 분봉이 없어 지나치면
        # 이후 첫 분봉에 체결 — force_by_close가 마감 잔량을 마저 잡는다.)
        target_elapsed = max(0, min(SESSION_MIN, cfg.at_min - KRX_OPEN_MIN))
        return remaining if elapsed_min >= target_elapsed else 0

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
