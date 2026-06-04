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
BROKERS = ("kis", "toss")
DEFAULT_BROKER = "kis"

# 브로커별 시크릿. SECRET_SPECS(pykrx, 항상 필요)와 분리 — 선택한 브로커 것만 요구/표시한다.
# env 변수명은 네이티브 표준이 없으므로 BUYLOW_ 접두로 통일한다.
BROKER_SECRET_SPECS: dict[str, list[SecretSpec]] = {
    "kis": [
        SecretSpec("kis_app_key", "BUYLOW_KIS_APP_KEY", "KIS App Key",
                   "한국투자증권 OpenAPI appkey"),
        SecretSpec("kis_app_secret", "BUYLOW_KIS_APP_SECRET", "KIS App Secret",
                   "한국투자증권 OpenAPI appsecret"),
        SecretSpec("kis_account_no", "BUYLOW_KIS_ACCOUNT_NO", "KIS 계좌번호",
                   "주문/잔고용 종합계좌번호 (예: 12345678-01). 라이브 단계에서 사용"),
    ],
    # Toss API 개방 시 동일 패턴으로 추가.
    "toss": [],
}


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
# 자동매매 on/off + 안전장치(무장·환경·주문한도). 실주문은 LEAN 라이브 + KIS 어댑터(adapter/)가 집행.
# 안전 원칙(docs/LIVE_KIS.md): real(실전) 환경에서 enabled는 armed=True일 때만 유효하고, 주문 1건은
# max_order_amount(원) 이하만 허용한다. 기본은 비활성·미무장·모의(demo)로 둬 사고를 막는다.
LIVE_KEYS = ("enabled", "armed", "env", "max_order_amount", "hts_id")
DEFAULT_LIVE = {"enabled": False, "armed": False, "env": "demo",
                "max_order_amount": 0, "hts_id": ""}


def get_live_config() -> dict:
    """라이브 설정. env BUYLOW_LIVE_* → config.local.yaml live: → 기본값."""
    lc = _load_local().get("live") or {}

    def _b(key):
        ev = os.environ.get(f"BUYLOW_LIVE_{key.upper()}")
        v = ev if ev is not None else lc.get(key, DEFAULT_LIVE[key])
        return str(v).strip().lower() in ("1", "true", "yes", "on") if isinstance(v, str) else bool(v)

    env = (os.environ.get("BUYLOW_LIVE_ENV") or lc.get("env") or DEFAULT_LIVE["env"]).lower()
    if env not in ("real", "demo"):
        env = "demo"
    try:
        max_amt = int(float(os.environ.get("BUYLOW_LIVE_MAX_ORDER_AMOUNT")
                            or lc.get("max_order_amount") or 0))
    except (TypeError, ValueError):
        max_amt = 0
    return {
        "enabled": _b("enabled"),
        "armed": _b("armed"),
        "env": env,
        "max_order_amount": max(0, max_amt),
        "hts_id": os.environ.get("BUYLOW_LIVE_HTS_ID") or lc.get("hts_id") or "",
    }


def save_live_config(values: dict) -> None:
    """대시보드 라이브 폼 저장. enabled/armed=불리언, env=real|demo, max_order_amount=원, hts_id=문자열."""
    data = _load_local()
    live = data.setdefault("live", {})

    def _truthy(v):
        return bool(v) if not isinstance(v, str) else v.strip().lower() in ("1", "true", "yes", "on")

    if "enabled" in values:
        live["enabled"] = _truthy(values.get("enabled"))
    if "armed" in values:
        live["armed"] = _truthy(values.get("armed"))
    if "env" in values:
        env = str(values.get("env") or "demo").lower()
        live["env"] = env if env in ("real", "demo") else "demo"
    if "max_order_amount" in values:
        try:
            live["max_order_amount"] = max(0, int(float(values.get("max_order_amount") or 0)))
        except (TypeError, ValueError):
            live["max_order_amount"] = 0
    if "hts_id" in values:
        live["hts_id"] = str(values.get("hts_id") or "").strip()
    _write_local(data)


def set_live_enabled(enabled: bool) -> None:
    save_live_config({"enabled": bool(enabled)})


def set_live_armed(armed: bool) -> None:
    save_live_config({"armed": bool(armed)})


def live_arming_ok(cfg: dict | None = None) -> tuple[bool, str]:
    """실주문 안전 가드. (허용여부, 사유). real+enabled는 armed 필수."""
    cfg = cfg or get_live_config()
    if not cfg["enabled"]:
        return False, "자동매매가 꺼져 있습니다"
    if cfg["env"] == "real" and not cfg["armed"]:
        return False, "실전(real) 자동매매는 무장(arming) 후에만 시작할 수 있습니다"
    return True, "ok"


def get_scheduler_config() -> dict:
    """일일 증분 적재 스케줄 설정. 기본 비활성(사용자가 켜야 자동 적재)."""
    sc = _load_local().get("scheduler") or {}
    return {
        "enabled": bool(sc.get("enabled", False)),
        "market": sc.get("market", "KOSPI200"),
        "hour": int(sc.get("hour", 18)),  # 평일 장마감 후 (KST)
    }


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


def get_kis_credentials() -> dict[str, str | None]:
    """KIS 자격증명 묶음 (없는 값은 None). app_key/app_secret/account_no."""
    by_key = {s.key: get_secret(s) for s in BROKER_SECRET_SPECS["kis"]}
    return {
        "app_key": by_key.get("kis_app_key"),
        "app_secret": by_key.get("kis_app_secret"),
        "account_no": by_key.get("kis_account_no"),
    }


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


def apply_krx_credentials() -> bool:
    """pykrx가 읽도록 KRX_ID/KRX_PW를 환경변수에 주입. 둘 다 있으면 True(=로그인 가능)."""
    kid = get_secret(SECRET_SPECS[0])
    kpw = get_secret(SECRET_SPECS[1])
    if kid and kpw:
        os.environ["KRX_ID"] = kid
        os.environ["KRX_PW"] = kpw
        return True
    return False
