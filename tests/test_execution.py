"""장중 타이밍 순수 로직 단위테스트 (LEAN 비의존)."""

from datetime import date

from orchestrator.execution import (
    TimingConfig, IMMEDIATE, PULLBACK, TWAP,
    minutes_since_open, is_last_bar, slice_index, decide_submit,
    should_daily_gate_eval,
    parse_eval_times, due_by_interval, due_by_times,
)


def test_should_daily_gate_eval():
    d1, d2 = date(2026, 6, 1), date(2026, 6, 2)
    assert not should_daily_gate_eval(10, 0, d1, None)     # 장중 → 평가 안 함
    assert should_daily_gate_eval(15, 29, d1, None)        # 마감 분봉 → 평가
    assert not should_daily_gate_eval(15, 30, d1, d1)      # 같은 날 이미 평가 → 중복 방지
    assert should_daily_gate_eval(15, 29, d2, d1)          # 다음 날 마감 → 평가


def test_parse_eval_times():
    # "HH:MM" → 자정기준 분, 정렬·중복제거, 잘못된 항목 무시
    assert parse_eval_times(["09:30", "10:05", "09:30"]) == (9 * 60 + 30, 10 * 60 + 5)
    assert parse_eval_times(["bad", "25:00", "12:01", ""]) == (12 * 60 + 1,)
    assert parse_eval_times(None) == ()


def test_due_by_interval():
    # 당일 첫 평가(None)는 항상 True, 이후엔 interval 이상 경과해야 True
    assert due_by_interval(0, 30, None) is True
    assert due_by_interval(10, 30, 0) is False        # 10분밖에 안 지남
    assert due_by_interval(30, 30, 0) is True         # 정확히 30분
    assert due_by_interval(65, 30, 30) is True        # 35분 경과(누적차 ≥ 30)
    assert due_by_interval(31, 30, 30) is False       # 1분 경과


def test_due_by_times():
    times = parse_eval_times(["09:30", "12:01"])
    fired = set()
    assert due_by_times(9 * 60 + 30, times, fired) is True    # 지정 시각
    assert due_by_times(9 * 60 + 31, times, fired) is False   # 비지정
    fired.add(9 * 60 + 30)
    assert due_by_times(9 * 60 + 30, times, fired) is False   # 당일 이미 평가
    assert due_by_times(12 * 60 + 1, times, fired) is True    # 다른 지정 시각


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


# ── TIME(특정시각) ───────────────────────────────────────────────────────────
def test_time_fills_at_target_time():
    from orchestrator.execution import TIME
    cfg = TimingConfig(style=TIME, at_min=13 * 60)  # 13:00 = 자정기준 780분
    common = dict(remaining=10, total_delta=10, current_price=100,
                  reference_price=100, last_bar=False)
    # 13:00 = 장시작(09:00) 후 240분. 그 전이면 대기, 도달하면 전량.
    assert decide_submit(cfg, elapsed_min=239, **common) == 0    # 12:59
    assert decide_submit(cfg, elapsed_min=240, **common) == 10   # 13:00 → 전량
    assert decide_submit(cfg, elapsed_min=300, **common) == 10   # 이후도 전량(놓쳤으면 마저)


def test_time_sell_sign_preserved():
    from orchestrator.execution import TIME
    cfg = TimingConfig(style=TIME, at_min=15 * 60 + 15)  # 15:15
    q = decide_submit(cfg, remaining=-7, total_delta=-7, current_price=100,
                      reference_price=100, elapsed_min=375, last_bar=False)  # 15:15=375분 경과
    assert q == -7


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
