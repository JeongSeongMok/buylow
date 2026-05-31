# 전략 Alpha 팩토리 — LEAN 내장 AlphaModel을 친숙한 이름/파라미터로 생성.
#
# 커스텀 Alpha는 두지 않는다(검증된 LEAN 내장을 재사용). 한국 특화(수급·저PBR 등 LEAN에 없는 것)는
# 추후 필요할 때만 커스텀으로 추가한다. orchestrator/strategy_catalog.py 의 스펙과 이름/키가 일치.
#
# 파라미터는 위치인자로 넘긴다(LEAN 모델마다 인자명이 snake/camel로 달라 키워드는 불안정).
# resolution은 기본값(DAILY)을 사용 — 우리 데이터가 일봉이므로 적합.
from AlgorithmImports import *


def build_alpha(name: str, p: dict):
    if name == "ema_cross":
        return EmaCrossAlphaModel(int(p["fast"]), int(p["slow"]))
    if name == "macd":
        return MacdAlphaModel(int(p["fast"]), int(p["slow"]), int(p["signal"]))
    if name == "rsi":
        return RsiAlphaModel(int(p["period"]))
    if name == "momentum":
        return HistoricalReturnsAlphaModel(int(p["lookback"]))
    if name == "flow":  # 한국 특화 커스텀(수급)
        from custom_alphas import FlowFollowingAlpha
        return FlowFollowingAlpha(int(p["lookback"]))
    if name == "value":  # 한국 특화 커스텀(저PBR 가치)
        from custom_alphas import ValueAlpha
        return ValueAlpha(float(p["max_pbr"]), int(p["period_days"]))
    raise ValueError(f"알 수 없는 alpha: {name}")
