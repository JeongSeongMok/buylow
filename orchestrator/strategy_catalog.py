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


CATALOG: list[AlphaSpec] = [
    AlphaSpec("ema_cross", "EMA 교차", "추세추종", "단기 EMA가 장기 EMA 위면 롱", [
        ParamSpec("fast", "단기 EMA", "int", 20),
        ParamSpec("slow", "장기 EMA", "int", 60),
        ParamSpec("period_days", "신호 유지(일)", "int", 5),
    ]),
    AlphaSpec("bnf", "BNF 이격도", "평균회귀", "이동평균 대비 과대낙폭이면 롱(반등)", [
        ParamSpec("ma", "이동평균(일)", "int", 25),
        ParamSpec("threshold", "이격 임계(비율)", "float", 0.12),
        ParamSpec("period_days", "신호 유지(일)", "int", 5),
    ]),
    AlphaSpec("rsi", "RSI 역추세", "평균회귀", "RSI 과매도 아래면 롱", [
        ParamSpec("period", "RSI 기간", "int", 14),
        ParamSpec("oversold", "과매도 기준", "float", 30),
        ParamSpec("period_days", "신호 유지(일)", "int", 5),
    ]),
    AlphaSpec("momentum", "모멘텀", "모멘텀", "최근 수익률(ROC) 양수면 롱", [
        ParamSpec("lookback", "관측(일)", "int", 120),
        ParamSpec("period_days", "신호 유지(일)", "int", 20),
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
