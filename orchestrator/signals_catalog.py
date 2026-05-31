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
# 설명은 사용자 친화 표현(매수/매도/중립)만 — 내부 신호값(UP/DOWN/NONE)은 노출하지 않는다.
CATALOG: list[SignalSpec] = [
    SignalSpec("ema", "EMA", "이동평균 추세", "단기 이동평균이 장기 이동평균보다 높으면 매수 우호, 낮으면 매도 우호", [
        ParamSpec("fast", "단기 이동평균", "int", 12),
        ParamSpec("slow", "장기 이동평균", "int", 26),
    ]),
    SignalSpec("macd", "MACD", "MACD", "MACD선이 시그널선 위면 매수 우호, 아래면 매도 우호", [
        ParamSpec("fast", "단기", "int", 12),
        ParamSpec("slow", "장기", "int", 26),
        ParamSpec("signal", "시그널", "int", 9),
    ]),
    SignalSpec("rsi", "RSI", "RSI 과열도", "과매도 구간이면 매수 우호, 과매수 구간이면 매도 우호, 중간은 중립", [
        ParamSpec("period", "기간", "int", 14),
        ParamSpec("oversold", "과매도(%)", "float", 30),
        ParamSpec("overbought", "과매수(%)", "float", 70),
    ]),
    SignalSpec("momentum", "MOM", "모멘텀", "최근 N일 수익률이 플러스면 매수 우호, 마이너스면 매도 우호", [
        ParamSpec("lookback", "관측(일)", "int", 60),
    ]),
    # flow / value 는 데이터 연동 후 추가 예정
]

DEFAULT_RULE = "(EMA AND MACD) OR RSI"
DEFAULT_PERIOD_DAYS = 3

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


def signals_from_form(form) -> dict:
    """폼(라벨__파라미터)에서 전체 signal 구성을 추출(식에 안 쓰여도 모두 보존)."""
    return {
        s.label: {"type": s.type, "params": cast_params(s.label, form)}
        for s in CATALOG
    }


DEFAULT_GROUPS = [["EMA", "MACD"], ["RSI"]]  # (EMA AND MACD) OR RSI


def rule_from_groups(groups: list[list[str]]) -> str:
    """그룹 목록 → 규칙식. 그룹 안은 AND, 그룹끼리는 OR (사용자 친화 빌더의 출력).

    예: [["EMA","MACD"],["RSI"]] → "(EMA AND MACD) OR RSI". 라벨은 카탈로그 순서로 정렬.
    """
    order = [s.label for s in CATALOG]
    parts = []
    for g in groups:
        labels = [l for l in order if l in set(g)]
        if not labels:
            continue
        parts.append("(" + " AND ".join(labels) + ")" if len(labels) > 1 else labels[0])
    return " OR ".join(parts)


def groups_from_form(form) -> list[list[str]]:
    """폼의 체크박스(g{그룹번호}_{라벨})에서 그룹 구조를 복원. 빈 그룹/미정의 라벨은 제외."""
    import re
    valid = {s.label for s in CATALOG}
    order = [s.label for s in CATALOG]
    by_idx: dict[int, set] = {}
    for key in form.keys():
        m = re.match(r"g(\d+)_(.+)$", key)
        if m and m.group(2) in valid and form.get(key):
            by_idx.setdefault(int(m.group(1)), set()).add(m.group(2))
    groups = []
    for gi in sorted(by_idx):
        labels = [l for l in order if l in by_idx[gi]]
        if labels:
            groups.append(labels)
    return groups


def default_strategy() -> dict:
    """기본 전략 스펙 — 저장된 게 없을 때 폼 초기값으로 사용."""
    signals = {
        s.label: {"type": s.type, "params": {p.key: p.default for p in s.params}}
        for s in CATALOG
    }
    return {"signals": signals, "rule": rule_from_groups(DEFAULT_GROUPS),
            "groups": DEFAULT_GROUPS, "period_days": DEFAULT_PERIOD_DAYS}


def param_value(strategy: dict, label: str, key: str):
    """저장된 전략에서 특정 signal 파라미터 값(폼 프리필용). 없으면 카탈로그 기본값."""
    try:
        return strategy["signals"][label]["params"][key]
    except (KeyError, TypeError):
        for p in _BY_LABEL[label].params:
            if p.key == key:
                return p.default
        return ""
