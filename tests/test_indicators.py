"""순수 지표 단위테스트 — 알려진 수열로 검증."""

import math

from orchestrator import indicators as ind


def test_sma_and_insufficient():
    assert ind.sma([1, 2, 3, 4], 2) == 3.5
    assert ind.sma([1], 2) is None


def test_ema_matches_manual():
    # period=3 → k=0.5, seed=SMA(1,2,3)=2, 다음 4: 4*0.5+2*0.5=3, 다음 5: 5*0.5+3*0.5=4
    assert ind.ema([1, 2, 3, 4, 5], 3) == 4.0
    assert ind.ema([1, 2], 3) is None


def test_roc():
    assert round(ind.roc([10, 11], 1), 6) == 10.0   # +10%
    assert round(ind.roc([100, 90], 1), 6) == -10.0
    assert ind.roc([10], 1) is None


def test_stddev_population():
    # [2,4,4,4,5,5,7,9] 모집단 std = 2.0
    assert round(ind.stddev([2, 4, 4, 4, 5, 5, 7, 9], 8), 6) == 2.0


def test_bollinger():
    vals = [2, 4, 4, 4, 5, 5, 7, 9]
    up, mid, lo = ind.bollinger(vals, 8, 2.0)
    assert mid == 5.0 and round(up, 6) == 9.0 and round(lo, 6) == 1.0  # 5 ± 2*2


def test_rsi_all_gains_is_100():
    assert ind.rsi([1, 2, 3, 4, 5, 6], 3) == 100.0


def test_rsi_known_value():
    # 단조 상승 후 하락 — 0~100 범위 + 하락 반영 확인
    vals = [10, 11, 12, 11, 10, 9]
    r = ind.rsi(vals, 3)
    assert 0 <= r <= 100 and r < 50  # 최근 하락 우세 → 50 미만


def test_macd_sign():
    # 가속 상승 → MACD선 > 0, 시그널선 위(선형이면 MACD선이 상수라 같아져서 가속 수열 사용)
    vals = [i * i for i in range(1, 40)]
    line, sig = ind.macd(vals, 12, 26, 9)
    assert line > 0 and sig is not None and line > sig


def test_macd_insufficient_signal():
    vals = list(range(1, 30))  # slow=26은 되지만 signal EMA 길이 부족할 수 있음
    res = ind.macd(vals, 12, 26, 9)
    assert res is not None and res[0] is not None  # 시그널은 None일 수 있음


def test_provisional_use_current_price():
    # 장중 모드 시뮬: 어제까지 종가 + 현재가를 덧붙여 재계산
    closes = [10, 11, 12, 13]
    intraday = ind.ema(closes + [14], 3)   # 현재가 14를 잠정 종가로
    assert intraday == 14 * 0.5 + ind.ema(closes, 3) * 0.5
