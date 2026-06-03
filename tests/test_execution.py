"""장중 타이밍 순수 로직 단위테스트 (LEAN 비의존)."""

from orchestrator.execution import (
    TimingConfig, IMMEDIATE, PULLBACK, TWAP,
    minutes_since_open, is_last_bar, slice_index, decide_submit,
)


# ── 시간 헬퍼 ──────────────────────────────────────────────────────────────
def test_minutes_since_open():
    assert minutes_since_open(9, 0) == 0
    assert minutes_since_open(9, 30) == 30
    assert minutes_since_open(8, 0) == 0      # 장전 클램프
    assert minutes_since_open(15, 30) == 390  # 마감
    assert minutes_since_open(16, 0) == 390   # 장후 클램프


def test_is_last_bar():
    assert not is_last_bar(15, 0)
    assert is_last_bar(15, 29)
    assert is_last_bar(15, 30)


def test_slice_index():
    # 390분을 6분할 → 각 65분
    assert slice_index(0, 6) == 0
    assert slice_index(64, 6) == 0
    assert slice_index(65, 6) == 1
    assert slice_index(390, 6) == 5  # 끝은 마지막 인덱스로 클램프


# ── IMMEDIATE ─────────────────────────────────────────────────────────────
def test_immediate_fills_all():
    cfg = TimingConfig(style=IMMEDIATE)
    q = decide_submit(cfg, remaining=10, total_delta=10, current_price=100,
                      reference_price=100, elapsed_min=0, last_bar=False)
    assert q == 10


# ── PULLBACK 매수 ──────────────────────────────────────────────────────────
def test_pullback_buy_waits_until_dip():
    cfg = TimingConfig(style=PULLBACK, entry_drop_pct=1.0)
    common = dict(remaining=10, total_delta=10, reference_price=100,
                  elapsed_min=10, last_bar=False)
    # 아직 안 눌림 → 미체결
    assert decide_submit(cfg, current_price=99.5, **common) == 0
    # 정확히 -1% 도달 → 전량 진입
    assert decide_submit(cfg, current_price=99.0, **common) == 10
    # 더 눌림 → 전량
    assert decide_submit(cfg, current_price=98.0, **common) == 10


def test_pullback_sell_waits_until_rebound():
    cfg = TimingConfig(style=PULLBACK, exit_rebound_pct=2.0)
    common = dict(remaining=-5, total_delta=-5, reference_price=100,
                  elapsed_min=10, last_bar=False)
    assert decide_submit(cfg, current_price=101.0, **common) == 0   # 반등 부족
    assert decide_submit(cfg, current_price=102.0, **common) == -5  # +2% 반등 → 전량 청산


def test_pullback_force_by_close():
    cfg = TimingConfig(style=PULLBACK, entry_drop_pct=5.0, force_by_close=True)
    # 트리거 미발생이어도 마지막 분봉이면 잔량 전량
    q = decide_submit(cfg, remaining=7, total_delta=7, current_price=100,
                      reference_price=100, elapsed_min=390, last_bar=True)
    assert q == 7


def test_pullback_no_force_when_disabled():
    cfg = TimingConfig(style=PULLBACK, entry_drop_pct=5.0, force_by_close=False)
    q = decide_submit(cfg, remaining=7, total_delta=7, current_price=100,
                      reference_price=100, elapsed_min=390, last_bar=True)
    assert q == 0  # 미체결 그대로


# ── TWAP ───────────────────────────────────────────────────────────────────
def test_twap_schedules_across_session():
    cfg = TimingConfig(style=TWAP, slices=4, force_by_close=True)
    # 총 100주, 4분할 → 각 슬라이스 누적 25/50/75/100
    # slice 0 시작, 아직 아무것도 안 채움(filled=0) → 25 제출
    q0 = decide_submit(cfg, remaining=100, total_delta=100, current_price=100,
                       reference_price=100, elapsed_min=0, last_bar=False)
    assert q0 == 25
    # slice 1(390/4≈97.5분 경과), 이미 25 체결(remaining=75) → 누적 50 - 25 = 25
    q1 = decide_submit(cfg, remaining=75, total_delta=100, current_price=100,
                       reference_price=100, elapsed_min=98, last_bar=False)
    assert q1 == 25


def test_twap_last_bar_dumps_remainder():
    cfg = TimingConfig(style=TWAP, slices=4)
    q = decide_submit(cfg, remaining=40, total_delta=100, current_price=100,
                      reference_price=100, elapsed_min=390, last_bar=True)
    assert q == 40  # 마지막엔 잔량 전부


def test_twap_sell_sign_preserved():
    cfg = TimingConfig(style=TWAP, slices=2, force_by_close=True)
    # 청산 -10, 2분할, slice 0 → 누적 -5
    q = decide_submit(cfg, remaining=-10, total_delta=-10, current_price=100,
                      reference_price=100, elapsed_min=0, last_bar=False)
    assert q == -5


def test_twap_never_exceeds_remaining():
    cfg = TimingConfig(style=TWAP, slices=1)
    q = decide_submit(cfg, remaining=10, total_delta=100, current_price=100,
                      reference_price=100, elapsed_min=0, last_bar=False)
    assert q == 10  # 스케줄(100)이 잔량 초과 → 잔량으로 클램프


def test_zero_remaining_is_noop():
    for style in (IMMEDIATE, PULLBACK, TWAP):
        cfg = TimingConfig(style=style)
        assert decide_submit(cfg, remaining=0, total_delta=0, current_price=100,
                             reference_price=100, elapsed_min=10, last_bar=False) == 0


def test_for_availability_fallback_to_immediate():
    cfg = TimingConfig(style=PULLBACK, entry_drop_pct=5.0)
    # 분봉 있음 → 설정 스타일(pullback): 안 눌렸으면 미체결
    avail = cfg.for_availability(True)
    assert avail.style == PULLBACK
    assert decide_submit(avail, remaining=10, total_delta=10, current_price=100,
                         reference_price=100, elapsed_min=10, last_bar=False) == 0
    # 분봉 없음 → 시가 즉시(immediate): 첫 호출에 전량
    fb = cfg.for_availability(False)
    assert fb.style == "immediate"
    assert decide_submit(fb, remaining=10, total_delta=10, current_price=100,
                         reference_price=100, elapsed_min=0, last_bar=False) == 10


def test_unknown_style_falls_back_to_pullback():
    cfg = TimingConfig(style="bogus", entry_drop_pct=1.0)
    # normalized() → pullback. 안 눌렸으면 0
    assert decide_submit(cfg, remaining=10, total_delta=10, current_price=100,
                         reference_price=100, elapsed_min=10, last_bar=False) == 0
