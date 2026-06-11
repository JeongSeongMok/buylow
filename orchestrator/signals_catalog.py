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
    SignalSpec("ema", "EMA", "이동평균 추세(EMA 교차)",
               "단기·장기 지수이동평균(EMA)의 교차로 추세를 본다.\n"
               "단기선이 장기선 위에 있으면 상승 추세로 보고 매수 우호, 아래면 매도 우호.\n"
               "숫자가 작을수록 가격 변화에 민감(잦은 신호), 클수록 둔감(추세 위주).", [
        ParamSpec("fast", "단기 이동평균(일)", "int", 12),
        ParamSpec("slow", "장기 이동평균(일)", "int", 26),
    ]),
    SignalSpec("macd", "MACD", "MACD (추세·모멘텀)",
               "단기 EMA와 장기 EMA의 차이(MACD선)와 그 평활선(시그널선)을 비교한다.\n"
               "MACD선이 시그널선 위로 올라오면 상승 모멘텀으로 보고 매수 우호, 아래로 내려가면 매도 우호.\n"
               "EMA 교차보다 전환을 빨리 잡는 편.", [
        ParamSpec("fast", "단기(일)", "int", 12),
        ParamSpec("slow", "장기(일)", "int", 26),
        ParamSpec("signal", "시그널(일)", "int", 9),
    ]),
    SignalSpec("rsi", "RSI", "RSI 과열도(역추세)",
               "최근 상승/하락 강도를 0~100으로 나타낸다.\n"
               "과매도(하한 아래)면 반등을 노려 매수 우호, 과매수(상한 위)면 매도 우호, 중간 구간은 중립.\n"
               "횡보장에서 잘 맞고 강한 추세장에선 일찍 반대 신호가 날 수 있음.", [
        ParamSpec("period", "기간(일)", "int", 14),
        ParamSpec("oversold", "과매도 기준(%)", "float", 30),
        ParamSpec("overbought", "과매수 기준(%)", "float", 70),
    ]),
    SignalSpec("momentum", "MOM", "모멘텀(수익률)",
               "최근 N일 수익률의 부호로 추세를 본다.\n"
               "N일 전보다 올랐으면 매수 우호, 내렸으면 매도 우호.\n"
               "단순하지만 강한 추세를 잘 따라감. N이 길수록 장기 추세, 짧을수록 단기 변동에 반응.", [
        ParamSpec("lookback", "관측 기간(일)", "int", 60),
    ]),
    SignalSpec("bollinger", "BB", "볼린저밴드(평균회귀+돌파 전환)",
               "이동평균 ± (표준편차×배수)로 밴드를 그린다.\n"
               "기본은 평균회귀 — 상단 터치=매도 우호, 하단 터치=매수 우호.\n"
               "다만 상단을 '돌파 전환 임계%' 이상 강하게 뚫으면 추세 시작으로 보고 매수로 전환, 하단을 그만큼 이탈하면 매도로 전환.", [
        ParamSpec("period", "기간(일)", "int", 20),
        ParamSpec("k", "표준편차 배수", "float", 2.0),
        ParamSpec("switch_pct", "돌파 전환 임계(%)", "float", 1.0),
    ]),
    SignalSpec("value", "VAL", "저평가(가치)",
               "펀더멘털(PER·PBR·배당)로 '싼 우량주'를 고른다.\n"
               "PER·PBR이 상한 이하이고 ROE(=PBR/PER)가 하한 이상이면 매수 우호.\n"
               "ROE 조건이 '싸기만 하고 돈은 못 버는' 가치 함정을 걸러준다.\n"
               "매수 필터형이라 단독보다 타이밍 신호와 함께 쓰면 좋음.\n"
               "(펀더멘털 데이터 적재 필요)", [
        ParamSpec("per_max", "PER 상한", "float", 10.0),
        ParamSpec("pbr_max", "PBR 상한", "float", 1.0),
        ParamSpec("roe_min", "ROE 하한(%)", "float", 8.0),
        ParamSpec("div_min", "배당수익률 하한(%)", "float", 0.0),
    ]),
    SignalSpec("flow", "FLOW", "수급 추종",
               "선택한 투자자(외국인·기관·개인)의 최근 N거래일 누적 순매수 방향을 따라간다.\n"
               "누적이 양수(순매수)면 매수 우호, 음수(순매도)면 매도 우호.\n"
               "개인은 보통 역지표라 기본 제외.\n"
               "N이 짧으면 신호가 자주 바뀌니 잡음이 많으면 N을 늘리세요.\n"
               "(수급 데이터 적재 필요)", [
        ParamSpec("lookback", "누적 기간(일)", "int", 7),
        ParamSpec("foreign", "외국인", "bool", 1),
        ParamSpec("institution", "기관", "bool", 1),
        ParamSpec("individual", "개인", "bool", 0),
    ]),
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
        key = f"{label}__{p.key}"
        if p.type == "bool":
            # 체크박스: 체크 시 폼에 키 존재(=1), 미체크 시 키 부재(=0). 기본값을 쓰지 않는다.
            out[p.key] = 1 if raw.get(key) else 0
            continue
        v = raw.get(key, "")
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


# ── 체결 타이밍(②층) ─────────────────────────────────────────────────────────
# 핵심 설계: 종목 선별은 항상 '전날 데이터 1회'로 고정(장중 재선별 없음). 그 위에 '언제/어떻게
# 체결할지'(타이밍)만 고른다. 타이밍이 해상도·리스크주기를 자동 결정한다:
#   - 시가/종가  → 일봉 데이터로 충분(분봉 구독 X) → 리스크=일별. 다음 거래일 시가/종가 체결.
#   - 특정시각/TWAP/눌림목 → 그날 매매할 종목만 분봉 구독 → 리스크=매분. 장중 타이밍 체결.
EXECUTION_TIMINGS = [
    ("open", "시가 — 다음 거래일 시가"),
    ("close", "종가 — 다음 거래일 종가(~15:15)"),
    ("time", "특정시각 — 그 시각에 체결"),
    ("twap", "TWAP — 장중 N분할 체결"),
    ("pullback", "눌림목 — 눌림 진입 / 반등 청산"),
]
_TIMING_KEYS = {k for k, _ in EXECUTION_TIMINGS}
_MINUTE_TIMINGS = {"time", "twap", "pullback"}  # 분봉 데이터가 필요한 타이밍

DEFAULT_EXECUTION = {"timing": "open", "at_time": "13:00", "slices": 6,
                     "entry_drop_pct": 1.0, "exit_rebound_pct": 1.0, "force_by_close": True,
                     # 아래는 타이밍에서 파생(RuleStrategy가 읽는 값) — 저장 시 함께 기록.
                     "select_eval": "close", "risk_eval": "daily", "daily_fill": "open",
                     "style": "twap", "at_min": 0}


def execution_from_form(form) -> tuple[str, dict]:
    """폼에서 '체결 타이밍'을 읽어 (resolution, execution)을 만든다.

    선별은 항상 전날 1회(select_eval=close). 타이밍이 나머지를 결정:
      timing ∈ {open, close, time, twap, pullback}
      - open/close → resolution=daily, risk=daily, daily_fill로 체결.
      - time/twap/pullback → resolution=minute, risk=bar, IntradayExecution 스타일로 체결
        (분봉은 그날 매매 대상·보유 종목만 구독 — 규모 한도 완화).
    """
    def num(key, default, cast):
        try:
            return cast(form.get(key, ""))
        except (TypeError, ValueError):
            return default

    timing = form.get("exec_timing", "open")
    if timing not in _TIMING_KEYS:
        timing = "open"
    is_minute = timing in _MINUTE_TIMINGS
    resolution = "minute" if is_minute else "daily"

    # 특정시각 정규화("HH:MM" → 분-of-day). 잘못된 값은 13:00.
    from .execution import parse_eval_times
    _m = parse_eval_times([(form.get("exec_at_time") or "13:00").strip()])
    at_min = _m[0] if _m else 13 * 60
    at_time = f"{at_min // 60:02d}:{at_min % 60:02d}"

    # IntradayExecutionModel 스타일(분봉 타이밍): time/twap/pullback 그대로 매핑.
    style = timing if is_minute else "twap"

    execution = {
        "timing": timing,
        "at_time": at_time, "at_min": at_min,
        "slices": max(1, num("exec_slices", 6, int)),
        "entry_drop_pct": num("exec_entry_drop_pct", 1.0, float),
        "exit_rebound_pct": num("exec_exit_rebound_pct", 1.0, float),
        "force_by_close": bool(form.get("exec_force_by_close")),
        # ── 파생값(RuleStrategy/실행모델이 읽음) ──
        "select_eval": "close",   # 선별은 항상 전날 1회
        # 리스크도 완성된 일봉(종가) 기준 1회로 통일 — 선별과 같은 철학. 분봉 매분 평가는 장중
        # 노이즈에 손절·트레일링이 계속 발동해 과매매(회전율 78%↑)를 일으키므로 쓰지 않는다.
        # 손절/익절 '판단'은 종가 1회, 그 '청산 체결'은 아래 타이밍이 처리한다.
        "risk_eval": "daily",
        "daily_fill": timing if timing in ("open", "close") else "open",
        "style": style,
    }
    return resolution, execution


def default_strategy() -> dict:
    """기본 전략 스펙 — 저장된 게 없을 때 폼 초기값으로 사용."""
    signals = {
        s.label: {"type": s.type, "params": {p.key: p.default for p in s.params}}
        for s in CATALOG
    }
    return {"signals": signals, "rule": rule_from_groups(DEFAULT_GROUPS),
            "groups": DEFAULT_GROUPS, "period_days": DEFAULT_PERIOD_DAYS,
            "resolution": "daily", "execution": dict(DEFAULT_EXECUTION)}


def param_value(strategy: dict, label: str, key: str):
    """저장된 전략에서 특정 signal 파라미터 값(폼 프리필용). 없으면 카탈로그 기본값."""
    try:
        return strategy["signals"][label]["params"][key]
    except (KeyError, TypeError):
        for p in _BY_LABEL[label].params:
            if p.key == key:
                return p.default
        return ""


def warmup_daily_bars(signals_config: dict) -> int:
    """선별 일봉 지표가 첫날부터 is_ready가 되도록 데울 일봉 수.

    선별 신호는 LEAN 일봉 지표(algo.ema(…, Resolution.DAILY) 등)를 쓴다. 라이브는 현재 시각부터
    시작(Warmup:False)이라 워밍업이 없으면 EMA60·MOM60 등이 영영 is_ready=False → 신호 0건 →
    주문이 안 나간다(백테스트는 시작 구간이 자연히 데워져 드러나지 않던 문제). 신호별 최대 룩백을
    구해 여유를 더한 만큼 일봉을 미리 흘려 지표를 데운다. 순수 함수라 단위테스트로 검증.
    """
    longest = 0
    for cfg in (signals_config or {}).values():
        params = cfg.get("params", {}) or {}
        if cfg.get("type") == "macd":  # MACD는 slow EMA 위에 signal EMA → 합산이 실제 룩백
            longest = max(longest, int(params.get("slow", 26)) + int(params.get("signal", 9)))
        for key in ("slow", "period", "lookback"):  # ema(slow)/rsi·bollinger(period)/momentum(lookback)
            if key in params:
                longest = max(longest, int(params[key]))
    # 지표 '준비'엔 룩백만큼 필요 + 표본 1~2개로 흔들리지 않게 여유(+10), 신호 없으면 최소 30.
    return max(longest + 10, 30)
