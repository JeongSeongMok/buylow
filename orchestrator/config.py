"""설정·시크릿 레이어.

해석 우선순위: **환경변수 → config.local.yaml → 기본값**.
시크릿(KRX ID/PW 등 BYO-key)은 repo에 안 들어간다(config.local.yaml은 gitignore). 대시보드
설정화면이 누락 시크릿을 입력받아 config.local.yaml에 저장한다(docs/ARCHITECTURE.md).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
# 테스트에서 다른 경로로 바꿔 끼울 수 있게 모듈 전역으로 둔다.
CONFIG_LOCAL = REPO_ROOT / "config.local.yaml"

DEFAULT_DASHBOARD_PORT = 8420
DEFAULT_DATA_FOLDER = REPO_ROOT / "data"


@dataclass(frozen=True)
class SecretSpec:
    """BYO 시크릿 1개의 메타. env 변수명은 해당 라이브러리가 읽는 네이티브 이름을 그대로 쓴다."""

    key: str       # config.local.yaml 의 secrets.<key>
    env: str       # 우선 적용 환경변수 (예: KRX_ID — pykrx가 직접 읽는 이름)
    label: str     # 대시보드 표시 라벨
    purpose: str   # 용도 설명


# pykrx 펀더멘털 조회에 항상 필요한 시크릿(브로커와 무관 — 무인증 시세와 별개로 PER/PBR은 KRX 로그인).
SECRET_SPECS: list[SecretSpec] = [
    SecretSpec("krx_id", "KRX_ID", "KRX 아이디", "pykrx 펀더멘털(PER/PBR) 조회 로그인"),
    SecretSpec("krx_pw", "KRX_PW", "KRX 비밀번호", "pykrx 펀더멘털 조회 로그인"),
]

# 지원 브로커. 사용자가 대시보드에서 자신의 증권사를 고른다.
# - 일봉 과거 데이터는 무인증 pykrx로 충분하므로 브로커 선택과 무관하게 적재된다.
# - 브로커는 "오늘(아직 미적재) 데이터"와 (라이브 단계의) 주문/실시간에만 관여한다.
# KIS는 실전/모의가 **앱키·서버가 완전히 분리**돼 있어 별도 증권사로 나눠 따로 관리한다
#   (kis=실전, kis_demo=모의투자). 둘은 같은 KisClient/KisBroker 로직을 공유하고 env만 다르다.
BROKERS = ("kis", "kis_demo", "toss")
DEFAULT_BROKER = "kis"

# 증권사 표시명.
BROKER_LABELS = {
    "kis": "한국투자증권 (KIS 실전)",
    "kis_demo": "한국투자증권 (KIS 모의투자)",
    "toss": "토스증권",
}


def broker_env(broker: str | None = None) -> str:
    """증권사 → KIS 환경(real/demo). kis_demo만 모의(demo), 나머지는 실전(real).

    ★ 단, '데이터(시세·분봉)'는 계좌가 필요 없어 항상 실전 도메인에서 받는다(brokers.kis.from_config
    env=real 고정). 이 env는 '매매(잔고·주문)' 도메인 결정에만 쓴다 — 모의 계좌는 모의 서버에서만 조회/주문.
    """
    return "demo" if (broker or get_broker()) == "kis_demo" else "real"

# 브로커별 시크릿. SECRET_SPECS(pykrx, 항상 필요)와 분리 — 선택한 브로커 것만 요구/표시한다.
# env 변수명은 네이티브 표준이 없으므로 BUYLOW_ 접두로 통일한다.
# kis(실전)와 kis_demo(모의)는 키·계좌가 다르므로 시크릿을 완전히 분리해 따로 저장한다.
BROKER_SECRET_SPECS: dict[str, list[SecretSpec]] = {
    "kis": [
        SecretSpec("kis_app_key", "BUYLOW_KIS_APP_KEY", "KIS App Key",
                   "한국투자증권 실전 OpenAPI appkey"),
        SecretSpec("kis_app_secret", "BUYLOW_KIS_APP_SECRET", "KIS App Secret",
                   "한국투자증권 실전 OpenAPI appsecret"),
        SecretSpec("kis_account_no", "BUYLOW_KIS_ACCOUNT_NO", "KIS 계좌번호",
                   "실전 종합계좌번호 (예: 12345678-01)"),
        SecretSpec("kis_hts_id", "BUYLOW_KIS_HTS_ID", "KIS HTS ID",
                   "실시간 체결통보 구독에 필요(라이브 체결 자동확인). 라이브 매매 필수"),
    ],
    "kis_demo": [
        SecretSpec("kis_demo_app_key", "BUYLOW_KIS_DEMO_APP_KEY", "KIS 모의 App Key",
                   "한국투자증권 모의투자 OpenAPI appkey"),
        SecretSpec("kis_demo_app_secret", "BUYLOW_KIS_DEMO_APP_SECRET", "KIS 모의 App Secret",
                   "한국투자증권 모의투자 OpenAPI appsecret"),
        SecretSpec("kis_demo_account_no", "BUYLOW_KIS_DEMO_ACCOUNT_NO", "KIS 모의 계좌번호",
                   "모의투자 종합계좌번호 (예: 50012345-01)"),
        SecretSpec("kis_demo_hts_id", "BUYLOW_KIS_DEMO_HTS_ID", "KIS 모의 HTS ID",
                   "실시간 체결통보 구독에 필요(라이브 체결 자동확인). 라이브 매매 필수"),
    ],
    # Toss API 개방 시 동일 패턴으로 추가.
    "toss": [],
}

# 증권사 → KIS 자격증명 키 묶음(app_key/app_secret/account_no 시크릿 key 이름).
_KIS_CRED_KEYS = {
    "kis": ("kis_app_key", "kis_app_secret", "kis_account_no"),
    "kis_demo": ("kis_demo_app_key", "kis_demo_app_secret", "kis_demo_account_no"),
}

# 증권사 → HTS ID 시크릿 key. 체결통보 구독에 쓰며 실전/모의가 다를 수 있어 따로 둔다.
_KIS_HTS_KEYS = {"kis": "kis_hts_id", "kis_demo": "kis_demo_hts_id"}


def _load_local() -> dict:
    if CONFIG_LOCAL.exists():
        return yaml.safe_load(CONFIG_LOCAL.read_text(encoding="utf-8")) or {}
    return {}


def _write_local(data: dict) -> None:
    CONFIG_LOCAL.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )


def get_data_folder() -> str:
    """LEAN 데이터 폴더. env LEAN_DATA_DIR → config → 기본 ./data."""
    return (
        os.environ.get("LEAN_DATA_DIR")
        or _load_local().get("data_folder")
        or str(DEFAULT_DATA_FOLDER)
    )


def get_dashboard_port() -> int:
    return int(
        os.environ.get("BUYLOW_DASHBOARD_PORT")
        or _load_local().get("dashboard_port")
        or DEFAULT_DASHBOARD_PORT
    )


RISK_KEYS = ("stop_loss", "take_profit", "trailing")

# 리스크 폼 기본값(%). 한 번도 저장하지 않았을 때 대시보드에 미리 채워 보여준다.
# 엔진(get_risk_config)은 '저장된' 값만 반영하므로, 사용자가 저장해야 실제 적용된다.
DEFAULT_RISK = {"stop_loss": 7, "take_profit": 20, "trailing": 5}


def get_risk_config() -> dict:
    """전역 리스크 설정(%). 값이 없거나 0이면 해당 규칙 미적용(None)."""
    r = _load_local().get("risk") or {}
    out = {}
    for k in RISK_KEYS:
        v = r.get(k)
        try:
            f = float(v)
            out[k] = f if f > 0 else None
        except (TypeError, ValueError):
            out[k] = None
    return out


def risk_form_values() -> dict:
    """대시보드 리스크 폼 프리필용. 항상 실제 값을 채운다 — 저장값이 있으면 그 값,
    없거나 비어 있으면 기본값(DEFAULT_RISK). 폼엔 placeholder가 아니라 실제 숫자가 보인다."""
    rc = get_risk_config()
    return {k: (rc[k] if rc[k] is not None else DEFAULT_RISK[k]) for k in RISK_KEYS}


def save_risk(values: dict) -> None:
    """대시보드에서 받은 리스크 % 값을 config.local.yaml 의 risk 섹션에 저장(빈값=미적용)."""
    data = _load_local()
    risk = data.setdefault("risk", {})
    for k in RISK_KEYS:
        v = str(values.get(k, "")).strip()
        try:
            f = float(v)
            risk[k] = f if f > 0 else None
        except ValueError:
            risk[k] = None
    _write_local(data)


def get_strategy() -> dict | None:
    """저장된 단일 전략 스펙(signals/rule/period_days). 없으면 None.

    전략은 하나만 유지한다(전략 설정 탭에서 저장 → 백테스트 탭에서 실행).
    """
    return _load_local().get("strategy")


def save_strategy(spec: dict) -> None:
    """전략 스펙을 config.local.yaml 의 strategy 섹션에 저장(덮어쓰기 — 단일 전략)."""
    data = _load_local()
    data["strategy"] = spec
    _write_local(data)


# ── 라이브(실주문) 설정 ────────────────────────────────────────────────────
# 자동매매 on/off + 선택적 주문한도. 실주문은 LEAN 라이브 + KIS 어댑터(adapter/)가 집행.
# 무장(arming) 개념은 제거했다 — enabled=True면 실전(real)·모의(demo) 모두 바로 주문이 전송된다.
# max_order_amount(원)는 0이면 비활성, >0이면 1건 주문금액 상한(어댑터가 검사)이라 무장과 무관한 선택 안전장치.
# env는 live에 저장하지 않는다 — '선택한 증권사'(kis=real, kis_demo=demo)가 결정한다.
LIVE_KEYS = ("enabled", "max_order_amount")
DEFAULT_LIVE = {"enabled": False, "max_order_amount": 0}


def get_live_config() -> dict:
    """라이브 설정. env BUYLOW_LIVE_* → config.local.yaml live: → 기본값.

    env(real/demo)는 저장값이 아니라 **선택한 증권사에서 도출**(broker_env)해 함께 돌려준다 —
    매매(잔고/주문) 도메인 결정용. 데이터(시세·분봉)는 env와 무관하게 항상 실전 도메인.
    """
    lc = _load_local().get("live") or {}

    def _b(key):
        ev = os.environ.get(f"BUYLOW_LIVE_{key.upper()}")
        v = ev if ev is not None else lc.get(key, DEFAULT_LIVE[key])
        return str(v).strip().lower() in ("1", "true", "yes", "on") if isinstance(v, str) else bool(v)

    try:
        max_amt = int(float(os.environ.get("BUYLOW_LIVE_MAX_ORDER_AMOUNT")
                            or lc.get("max_order_amount") or 0))
    except (TypeError, ValueError):
        max_amt = 0
    return {
        "enabled": _b("enabled"),
        "env": broker_env(),  # 증권사에서 도출(저장 안 함)
        "max_order_amount": max(0, max_amt),
        "hts_id": get_kis_hts_id(),  # 설정 탭의 증권사별 시크릿에서(앱키와 동일 관리)
    }


def save_live_config(values: dict) -> None:
    """대시보드 라이브 폼 저장. enabled=불리언, max_order_amount=원.
    (env는 증권사가 결정, hts_id는 설정 탭 시크릿 — 여기선 저장하지 않는다.)"""
    data = _load_local()
    live = data.setdefault("live", {})

    def _truthy(v):
        return bool(v) if not isinstance(v, str) else v.strip().lower() in ("1", "true", "yes", "on")

    if "enabled" in values:
        live["enabled"] = _truthy(values.get("enabled"))
    if "max_order_amount" in values:
        try:
            live["max_order_amount"] = max(0, int(float(values.get("max_order_amount") or 0)))
        except (TypeError, ValueError):
            live["max_order_amount"] = 0
    _write_local(data)


def get_live_universe() -> list[str]:
    """라이브 자동매매 대상종목(코드 리스트). 매매 탭에서 선택해 저장. 해상도는 전략 spec을 따른다."""
    lc = _load_local().get("live") or {}
    uni = lc.get("universe") or []
    return [str(t).strip() for t in uni if str(t).strip()]


def save_live_universe(tickers: list[str]) -> None:
    """라이브 대상종목 저장(중복 제거, 순서 보존)."""
    seen, uniq = set(), []
    for t in tickers:
        t = str(t).strip()
        if t and t not in seen:
            seen.add(t); uniq.append(t)
    data = _load_local()
    data.setdefault("live", {})["universe"] = uniq
    _write_local(data)


def set_live_enabled(enabled: bool) -> None:
    save_live_config({"enabled": bool(enabled)})


def live_start_ok(cfg: dict | None = None) -> tuple[bool, str]:
    """자동매매 시작 가드. (허용여부, 사유). enabled=True면 실전·모의 모두 허용(무장 개념 제거).

    HTS ID는 필수 — 없으면 체결통보를 구독하지 못해 주문 체결이 LEAN에 자동 반영되지 않아
    포지션/리스크 추적이 어긋난다(설정 탭에서 증권사별로 등록). 실전·모의 모두 동일 적용."""
    cfg = cfg or get_live_config()
    if not cfg["enabled"]:
        return False, "자동매매가 꺼져 있습니다"
    if not cfg.get("hts_id"):
        return False, "HTS ID가 없습니다 — 설정 탭에서 등록하세요(체결 자동확인에 필수)"
    return True, "ok"


# ── 커스텀 인덱스(사용자 정의 종목 묶음) ──────────────────────────────────────
# 내장 인덱스(KOSPI200 등, etl.universe.INDEXES)와 동일하게 백테스트·데이터탭·적재현황에서 쓰도록
# 통합한다. 내장은 pykrx 지수코드로 구성종목을 조회하지만, 커스텀은 사용자가 묶은 종목을 그대로 쓴다.
# 저장은 사용자 데이터라 config.local.yaml(gitignore)에 둔다.

def get_custom_indices() -> dict:
    """커스텀 인덱스 {key: {"label": str, "tickers": [코드...]}}. 없으면 {}."""
    ci = _load_local().get("custom_indices") or {}
    return ci if isinstance(ci, dict) else {}


def save_custom_index(label: str, tickers) -> str:
    """커스텀 인덱스 저장(생성/덮어쓰기). key=label. 내장 인덱스명과 충돌하면 거부. 저장한 key 반환."""
    from etl.universe import INDEX_CODES
    label = (label or "").strip()
    if not label:
        raise ValueError("인덱스 이름을 입력하세요")
    if label.upper() in INDEX_CODES:
        raise ValueError(f"'{label}'은(는) 내장 인덱스명과 겹칩니다 — 다른 이름을 쓰세요")
    codes = [t.strip() for t in (tickers if isinstance(tickers, list) else str(tickers).split(","))]
    codes = [c for c in codes if c]
    if not codes:
        raise ValueError("종목을 하나 이상 추가하세요")
    # 중복 제거(입력 순서 보존)
    seen, uniq = set(), []
    for c in codes:
        if c not in seen:
            seen.add(c); uniq.append(c)
    data = _load_local()
    ci = data.setdefault("custom_indices", {})
    ci[label] = {"label": label, "tickers": uniq}
    _write_local(data)
    return label


def delete_custom_index(key: str) -> bool:
    """커스텀 인덱스 삭제. 삭제했으면 True."""
    data = _load_local()
    ci = data.get("custom_indices") or {}
    if key in ci:
        ci.pop(key)
        data["custom_indices"] = ci
        _write_local(data)
        return True
    return False


def all_indices() -> list[dict]:
    """내장 + 커스텀 인덱스 통합 목록 [{key, label, custom}]. 대시보드 동적 렌더용(SSOT 통합).

    내장(etl.universe.INDEXES)이 먼저, 커스텀이 뒤. 커스텀 라벨은 ★ 접두로 구분 표시.
    """
    from etl.universe import list_indices
    out = [{"key": i["key"], "label": i["label"], "custom": False} for i in list_indices()]
    for key, v in get_custom_indices().items():
        out.append({"key": key, "label": "★ " + (v.get("label") or key), "custom": True})
    return out


def get_scheduler_config() -> dict:
    """자동 적재 스케줄 설정. 기본 켜짐 — 서버 가동 중 일정 간격으로 '데이터 최신화'(pykrx 일봉)를
    반복한다. 채워져 있으면 증분이라 금방 끝나므로 짧은 간격으로 계속 돌려도 부담이 적다.
    `minute_universe`가 있으면 같은 스케줄에서 그 종목들의 분봉도 증분 적재한다(없으면 분봉은 생략)."""
    sc = _load_local().get("scheduler") or {}
    uni = sc.get("minute_universe") or []
    return {
        "enabled": bool(sc.get("enabled", True)),
        "interval_minutes": int(sc.get("interval_minutes", 30)),  # 연속 반복 간격
        "minute_universe": [str(t).strip() for t in uni if str(t).strip()],
        # 하위호환(과거 일일 cron 설정) — 더 이상 트리거엔 안 쓰지만 읽기 보존
        "market": sc.get("market", "KOSPI200"),
        "hour": int(sc.get("hour", 18)),
    }


def save_scheduler_minute_universe(tickers: list[str]) -> None:
    """자동 적재 스케줄러가 분봉을 적재할 대상종목 저장(중복 제거, 순서 보존). 빈 리스트면 분봉 생략."""
    seen, uniq = set(), []
    for t in tickers:
        t = str(t).strip()
        if t and t not in seen:
            seen.add(t); uniq.append(t)
    data = _load_local()
    data.setdefault("scheduler", {})["minute_universe"] = uniq
    _write_local(data)


def get_broker() -> str:
    """선택된 브로커. env BUYLOW_BROKER → config → 기본(kis). 미지원 값이면 기본으로."""
    b = (os.environ.get("BUYLOW_BROKER") or _load_local().get("broker") or DEFAULT_BROKER)
    return b if b in BROKERS else DEFAULT_BROKER


def set_broker(broker: str) -> None:
    if broker not in BROKERS:
        raise ValueError(f"알 수 없는 브로커: {broker} (가능: {list(BROKERS)})")
    data = _load_local()
    data["broker"] = broker
    _write_local(data)


def _all_specs() -> list[SecretSpec]:
    """pykrx 시크릿 + 모든 브로커 시크릿 (저장/조회용 전체 화이트리스트)."""
    out = list(SECRET_SPECS)
    for specs in BROKER_SECRET_SPECS.values():
        out.extend(specs)
    return out


def get_secret(spec: SecretSpec) -> str | None:
    """env 우선, 없으면 config.local.yaml 의 secrets.<key>."""
    return os.environ.get(spec.env) or (_load_local().get("secrets") or {}).get(spec.key)


def get_kis_credentials(broker: str | None = None) -> dict[str, str | None]:
    """선택(또는 지정) 증권사의 KIS 자격증명 묶음. app_key/app_secret/account_no (없으면 None).

    kis(실전)/kis_demo(모의)는 키가 분리돼 있어 증권사에 맞는 시크릿을 읽는다. KIS가 아니면(toss 등)
    실전(kis) 키로 폴백 — 데이터 계층(시세·분봉, env=real)이 항상 KIS 키를 쓰기 때문.
    """
    broker = broker or get_broker()
    ak_key, sk_key, acc_key = _KIS_CRED_KEYS.get(broker, _KIS_CRED_KEYS["kis"])
    by_key = {s.key: get_secret(s) for specs in BROKER_SECRET_SPECS.values() for s in specs}
    return {
        "app_key": by_key.get(ak_key),
        "app_secret": by_key.get(sk_key),
        "account_no": by_key.get(acc_key),
    }


def get_kis_hts_id(broker: str | None = None) -> str:
    """선택(또는 지정) 증권사의 HTS ID(체결통보용). 미설정이면 빈 문자열.

    app_key/secret처럼 설정 탭에서 증권사별로 등록하는 시크릿이다(실전/모의 분리). KIS가 아니면
    실전(kis) 키로 폴백 — 데이터 계층 일관성(get_kis_credentials와 동일 정책)."""
    broker = broker or get_broker()
    hts_key = _KIS_HTS_KEYS.get(broker, _KIS_HTS_KEYS["kis"])
    for specs in BROKER_SECRET_SPECS.values():
        for s in specs:
            if s.key == hts_key:
                return get_secret(s) or ""
    return ""


def broker_secret_status(broker: str | None = None) -> list[dict]:
    """선택(또는 지정) 브로커의 시크릿 설정여부 — 대시보드 표시용(값 비노출)."""
    broker = broker or get_broker()
    return [
        {"key": s.key, "label": s.label, "purpose": s.purpose, "set": bool(get_secret(s))}
        for s in BROKER_SECRET_SPECS.get(broker, [])
    ]


def secret_status() -> list[dict]:
    """대시보드 표시용: 각 시크릿의 라벨/용도/설정여부(값은 노출 안 함)."""
    return [
        {"key": s.key, "label": s.label, "purpose": s.purpose, "set": bool(get_secret(s))}
        for s in SECRET_SPECS
    ]


def missing_secrets() -> list[SecretSpec]:
    return [s for s in SECRET_SPECS if not get_secret(s)]


def save_secrets(values: dict[str, str]) -> None:
    """대시보드에서 받은 시크릿을 config.local.yaml 에 저장. 빈 값/미정의 키는 무시."""
    data = _load_local()
    secrets = data.setdefault("secrets", {})
    valid = {s.key for s in _all_specs()}
    for k, v in values.items():
        if k in valid and v:
            secrets[k] = v
    _write_local(data)


def clear_broker_secrets(broker: str) -> list[str]:
    """지정 증권사의 시크릿(앱키/시크릿/계좌)을 config.local.yaml 에서 삭제. 삭제한 key 목록 반환.

    실수로 잘못 넣은 키를 비우거나 증권사 전환 시 정리용. (env 변수로 설정된 값은 못 지운다 — 그쪽은
    셸에서 unset 해야 한다.)
    """
    data = _load_local()
    sec = data.get("secrets") or {}
    removed = []
    for s in BROKER_SECRET_SPECS.get(broker, []):
        if s.key in sec:
            sec.pop(s.key, None)
            removed.append(s.key)
    data["secrets"] = sec
    _write_local(data)
    return removed


def apply_krx_credentials() -> bool:
    """pykrx가 읽도록 KRX_ID/KRX_PW를 환경변수에 주입. 둘 다 있으면 True(=로그인 가능)."""
    kid = get_secret(SECRET_SPECS[0])
    kpw = get_secret(SECRET_SPECS[1])
    if kid and kpw:
        os.environ["KRX_ID"] = kid
        os.environ["KRX_PW"] = kpw
        return True
    return False
