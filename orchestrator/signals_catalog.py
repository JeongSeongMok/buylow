"""Signal 카탈로그 — 규칙 엔진의 부품 스펙(순수, LEAN 의존 없음).

각 signal은 UP/DOWN/NONE을 반환하는 조건. 대시보드가 이 스펙으로 파라미터 입력 UI를 만들고,
사용자는 라벨(EMA/MACD/...)로 불리언 식을 작성한다. 종류 추가 = 여기 1개 + strategies/signals.py 1개.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ParamSpec:
    key: str
    label: str
    type: str   # "int" | "float"
    default: float


@dataclass(frozen=True)
class SignalSpec:
    type: str          # strategies/signals.py 의 SIGNAL_TYPES 키와 일치
    label: str         # 식에서 쓰는 기본 라벨 (예: EMA)
    name: str          # 표시 이름
    description: str    # UP/DOWN 의미
    params: list[ParamSpec] = field(default_factory=list)


# 기본 라벨 = type 키를 대문자로. 식에서 EMA/MACD/RSI/MOM 으로 참조.
CATALOG: list[SignalSpec] = [
    SignalSpec("ema", "EMA", "EMA 추세", "단기 EMA > 장기 EMA = UP, 반대 = DOWN", [
        ParamSpec("fast", "단기 EMA", "int", 12),
        ParamSpec("slow", "장기 EMA", "int", 26),
    ]),
    SignalSpec("macd", "MACD", "MACD", "MACD선 > 시그널선 = UP, 반대 = DOWN", [
        ParamSpec("fast", "단기", "int", 12),
        ParamSpec("slow", "장기", "int", 26),
        ParamSpec("signal", "시그널", "int", 9),
    ]),
    SignalSpec("rsi", "RSI", "RSI", "과매도(<oversold)=UP, 과매수(>overbought)=DOWN, 중립=NONE", [
        ParamSpec("period", "기간", "int", 14),
        ParamSpec("oversold", "과매도(%)", "float", 30),
        ParamSpec("overbought", "과매수(%)", "float", 70),
    ]),
    SignalSpec("momentum", "MOM", "모멘텀", "최근 N일 수익률 > 0 = UP, < 0 = DOWN", [
        ParamSpec("lookback", "관측(일)", "int", 60),
    ]),
    # flow / value 는 데이터 연동 후 추가 예정
]

_BY_LABEL = {s.label: s for s in CATALOG}


def get_by_label(label: str) -> SignalSpec | None:
    return _BY_LABEL.get(label)


def cast_params(label: str, raw: dict[str, str]) -> dict:
    spec = _BY_LABEL[label]
    out: dict = {}
    for p in spec.params:
        v = raw.get(f"{label}__{p.key}", "")
        if v in ("", None):
            out[p.key] = p.default
        else:
            out[p.key] = int(v) if p.type == "int" else float(v)
    return out
