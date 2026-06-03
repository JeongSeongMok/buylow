"""순수 기술지표 — 종가 리스트(오래된→최신)로 값을 계산한다(LEAN 비의존, 단위테스트 가능).

용도: 장중 '선별' 재평가. 완성된 일봉 종가 윈도우에 **현재가를 잠정 종가로 덧붙여**
지표를 다시 계산하면, "오늘이 지금 끝났다면" 기준의 신호 방향을 매 분봉마다 얻을 수 있다.
(전날 종가 1회 선별은 기존 LEAN 지표 경로를 그대로 쓰고, 이 모듈은 '장중 매분' 모드에서만 쓴다.)

값이 모자라면 None을 반환한다. RSI/MACD는 Wilder/표준 EMA 정의를 따른다.
"""

from __future__ import annotations


def _ema_series(values: list[float], period: int) -> list[float]:
    """EMA 시계열(첫 period개의 SMA로 시드 후 갱신). 길이 = len-period+1, 부족하면 []."""
    if period <= 0 or len(values) < period:
        return []
    k = 2.0 / (period + 1)
    seed = sum(values[:period]) / period
    out = [seed]
    for v in values[period:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


def ema(values: list[float], period: int) -> float | None:
    s = _ema_series(values, period)
    return s[-1] if s else None


def sma(values: list[float], period: int) -> float | None:
    if len(values) < period or period <= 0:
        return None
    return sum(values[-period:]) / period


def stddev(values: list[float], period: int) -> float | None:
    """모집단 표준편차(분모 n) — 볼린저밴드 관례."""
    if len(values) < period or period <= 0:
        return None
    w = values[-period:]
    m = sum(w) / period
    return (sum((x - m) ** 2 for x in w) / period) ** 0.5


def bollinger(values: list[float], period: int, k: float):
    """(상단, 중심, 하단) 또는 None."""
    m = sma(values, period)
    sd = stddev(values, period)
    if m is None or sd is None:
        return None
    return (m + k * sd, m, m - k * sd)


def roc(values: list[float], period: int) -> float | None:
    """N기간 변화율(%) — momentum 신호용."""
    if len(values) < period + 1:
        return None
    prev = values[-1 - period]
    if prev == 0:
        return None
    return (values[-1] / prev - 1) * 100.0


def rsi(values: list[float], period: int) -> float | None:
    """Wilder RSI. 종가 period+1개 이상 필요."""
    if len(values) < period + 1:
        return None
    deltas = [values[i + 1] - values[i] for i in range(len(values) - 1)]
    first = deltas[:period]
    avg_gain = sum(d for d in first if d > 0) / period
    avg_loss = sum(-d for d in first if d < 0) / period
    for d in deltas[period:]:
        avg_gain = (avg_gain * (period - 1) + (d if d > 0 else 0)) / period
        avg_loss = (avg_loss * (period - 1) + (-d if d < 0 else 0)) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1 + rs)


def macd(values: list[float], fast: int, slow: int, signal: int):
    """(MACD선, 시그널선). 시그널 계산 불가하면 (MACD선, None), 전부 불가하면 None."""
    fs = _ema_series(values, fast)
    ss = _ema_series(values, slow)
    if not fs or not ss:
        return None
    offset = len(fs) - len(ss)  # fast가 더 일찍 시작 → tail 정렬
    macd_line = [fs[offset + i] - ss[i] for i in range(len(ss))]
    if len(macd_line) < signal:
        return (macd_line[-1], None)
    sig = _ema_series(macd_line, signal)
    return (macd_line[-1], sig[-1] if sig else None)
