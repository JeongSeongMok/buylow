"""전략(Alpha) 카탈로그 — 순수 스펙(LEAN/AlgorithmImports 의존 없음).

대시보드가 이 스펙으로 "어떤 Alpha를 어떤 파라미터로" 고르는 UI를 만들고, 사용자가 조합한
스펙을 strategies/Composed.py(범용 알고리즘)에 JSON 파라미터로 넘긴다.

⚠️ 이름(name)/파라미터 키는 strategies/alphas.py 의 `_ALPHA_CLASSES` 및 각 AlphaModel의
__init__ 인자와 일치해야 한다(여기 = 단일 스펙 출처).
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ParamSpec:
    key: str
    label: str
    type: str          # "int" | "float"
    default: float


@dataclass(frozen=True)
class AlphaSpec:
    name: str
    label: str
    category: str
    description: str
    params: list[ParamSpec] = field(default_factory=list)


# 전부 LEAN 내장 AlphaModel (검증됨). 파라미터 키는 우리 친숙한 이름 — alphas.build_alpha가
# 위치인자로 LEAN 생성자에 매핑한다. (한국 특화 Alpha는 추후 필요 시 추가)
CATALOG: list[AlphaSpec] = [
    AlphaSpec("ema_cross", "EMA 교차", "추세추종",
              "단/장기 EMA 교차 (LEAN EmaCrossAlphaModel)", [
                  ParamSpec("fast", "단기 EMA", "int", 12),
                  ParamSpec("slow", "장기 EMA", "int", 26),
              ]),
    AlphaSpec("macd", "MACD", "추세추종",
              "MACD 신호선 교차 (LEAN MacdAlphaModel)", [
                  ParamSpec("fast", "단기", "int", 12),
                  ParamSpec("slow", "장기", "int", 26),
                  ParamSpec("signal", "시그널", "int", 9),
              ]),
    AlphaSpec("rsi", "RSI", "평균회귀",
              "RSI 과매수/과매도 (LEAN RsiAlphaModel)", [
                  ParamSpec("period", "RSI 기간", "int", 14),
              ]),
    AlphaSpec("momentum", "모멘텀(과거수익률)", "모멘텀",
              "과거 수익률 부호 기반 (LEAN HistoricalReturnsAlphaModel)", [
                  ParamSpec("lookback", "관측(기간)", "int", 60),
              ]),
    # 한국 특화 커스텀 (LEAN 내장 아님) — 수급 데이터(etl.flow) 필요
    AlphaSpec("flow", "수급 추종(외국인)", "수급",
              "외국인 순매수 N일 누적이 양수면 롱 (커스텀; 수급 데이터 필요)", [
                  ParamSpec("lookback", "누적(일)", "int", 5),
              ]),
    AlphaSpec("value", "저PBR 가치", "가치",
              "PBR이 상한 미만이면 롱 (커스텀; 펀더멘털 데이터 필요)", [
                  ParamSpec("max_pbr", "PBR 상한", "float", 1.0),
                  ParamSpec("period_days", "보유(일)", "int", 20),
              ]),
]

_BY_NAME = {a.name: a for a in CATALOG}


def get_spec(name: str) -> AlphaSpec | None:
    return _BY_NAME.get(name)


def cast_params(name: str, raw: dict[str, str]) -> dict:
    """폼 문자열 입력을 스펙 타입으로 변환(미입력 시 기본값)."""
    spec = _BY_NAME.get(name)
    if spec is None:
        raise ValueError(f"알 수 없는 alpha: {name}")
    out: dict = {}
    for p in spec.params:
        v = raw.get(p.key, "")
        if v == "" or v is None:
            out[p.key] = p.default
        else:
            out[p.key] = int(v) if p.type == "int" else float(v)
    return out
