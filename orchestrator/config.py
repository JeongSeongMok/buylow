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


# 현재 필요한 시크릿. 라이브 단계에서 toss_*, ai_* 추가 예정.
SECRET_SPECS: list[SecretSpec] = [
    SecretSpec("krx_id", "KRX_ID", "KRX 아이디", "pykrx 펀더멘털(PER/PBR) 조회 로그인"),
    SecretSpec("krx_pw", "KRX_PW", "KRX 비밀번호", "pykrx 펀더멘털 조회 로그인"),
]


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


RISK_KEYS = ("stop_loss", "take_profit", "trailing", "max_drawdown")


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


def get_scheduler_config() -> dict:
    """일일 증분 적재 스케줄 설정. 기본 비활성(사용자가 켜야 자동 적재)."""
    sc = _load_local().get("scheduler") or {}
    return {
        "enabled": bool(sc.get("enabled", False)),
        "market": sc.get("market", "KOSPI200"),
        "hour": int(sc.get("hour", 18)),  # 평일 장마감 후 (KST)
    }


def get_secret(spec: SecretSpec) -> str | None:
    """env 우선, 없으면 config.local.yaml 의 secrets.<key>."""
    return os.environ.get(spec.env) or (_load_local().get("secrets") or {}).get(spec.key)


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
    valid = {s.key for s in SECRET_SPECS}
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
