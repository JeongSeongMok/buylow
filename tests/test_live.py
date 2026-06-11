"""라이브(실주문) 설정 + live-kis config 생성 단위 테스트 (LEAN/.NET·네트워크 없이).

실주문 경로의 설정(시작 가드·환경 분기·주문한도·KIS 자격증명 주입)을 검증한다. 무장 개념은 제거됨.
conftest의 _isolate_config가 config.local.yaml을 테스트마다 임시파일로 격리한다.
"""

from orchestrator import config
from orchestrator.lean.runner import RunRequest, build_live_config


# ── 라이브 설정 ─────────────────────────────────────────────────────────────
def test_live_config_defaults_are_safe():
    lc = config.get_live_config()
    assert lc["enabled"] is False      # 기본 꺼짐 → 거래 안 됨(유일한 시작 가드)
    assert "armed" not in lc           # 무장 개념 제거됨
    assert lc["env"] == "real"         # env는 기본 증권사(kis=실전)에서 도출
    assert lc["max_order_amount"] == 0


def test_env_is_derived_from_broker():
    # env는 더 이상 저장값이 아니라 '선택한 증권사'가 결정한다.
    config.set_broker("kis_demo")
    assert config.get_live_config()["env"] == "demo"
    assert config.broker_env() == "demo"
    config.set_broker("kis")
    assert config.get_live_config()["env"] == "real"


def test_custom_index_crud_and_all_indices():
    # 커스텀 인덱스 저장/조회/삭제 + 내장과 통합.
    config.save_custom_index("반도체3종", "005930,000660,005380")
    ci = config.get_custom_indices()
    assert ci["반도체3종"]["tickers"] == ["005930", "000660", "005380"]
    # all_indices: 내장 먼저, 커스텀 뒤(★ 라벨, custom 플래그)
    allx = config.all_indices()
    keys = [i["key"] for i in allx]
    assert "KOSPI200" in keys and "반도체3종" in keys
    custom = next(i for i in allx if i["key"] == "반도체3종")
    assert custom["custom"] is True and custom["label"].startswith("★")
    # 삭제
    assert config.delete_custom_index("반도체3종") is True
    assert "반도체3종" not in config.get_custom_indices()


def test_custom_index_rejects_bad_input():
    import pytest as _pt
    with _pt.raises(ValueError):
        config.save_custom_index("", "005930")          # 이름 없음
    with _pt.raises(ValueError):
        config.save_custom_index("그룹", "")             # 종목 없음
    with _pt.raises(ValueError):
        config.save_custom_index("KOSPI200", "005930")   # 내장명 충돌


def test_clear_broker_secrets_removes_only_that_broker():
    config.save_secrets({"kis_app_key": "RK", "kis_app_secret": "RS", "kis_account_no": "RA",
                         "kis_demo_app_key": "DK", "kis_demo_app_secret": "DS"})
    removed = config.clear_broker_secrets("kis")
    assert set(removed) == {"kis_app_key", "kis_app_secret", "kis_account_no"}
    assert config.get_kis_credentials("kis")["app_key"] is None        # 실전 삭제됨
    assert config.get_kis_credentials("kis_demo")["app_key"] == "DK"   # 모의는 보존


def test_kis_demo_credentials_are_separate(monkeypatch):
    # 실전(kis)과 모의(kis_demo)는 키를 분리해 저장/조회한다.
    config.save_secrets({"kis_app_key": "REALKEY", "kis_app_secret": "RS",
                         "kis_demo_app_key": "DEMOKEY", "kis_demo_app_secret": "DS"})
    assert config.get_kis_credentials("kis")["app_key"] == "REALKEY"
    assert config.get_kis_credentials("kis_demo")["app_key"] == "DEMOKEY"


def test_save_and_get_live_config():
    config.save_live_config({"enabled": True, "env": "real", "max_order_amount": 500000})
    # HTS ID는 라이브 폼이 아니라 설정 탭의 증권사 시크릿에서 온다(앱키와 동일 관리).
    config.set_broker("kis")
    config.save_secrets({"kis_hts_id": "myhts"})
    lc = config.get_live_config()
    assert lc["enabled"]
    assert lc["env"] == "real"
    assert lc["max_order_amount"] == 500000
    assert lc["hts_id"] == "myhts"


def test_hts_id_is_per_broker_secret():
    # 실전/모의 HTS ID가 분리 저장되고 활성 증권사에 따라 도출된다.
    config.save_secrets({"kis_hts_id": "REALHTS", "kis_demo_hts_id": "DEMOHTS"})
    assert config.get_kis_hts_id("kis") == "REALHTS"
    assert config.get_kis_hts_id("kis_demo") == "DEMOHTS"
    config.set_broker("kis_demo")
    assert config.get_live_config()["hts_id"] == "DEMOHTS"


def test_live_start_blocked_without_hts_id():
    # HTS ID 없으면 enabled여도 라이브 시작 거부(체결 자동확인 불가).
    config.set_broker("kis")
    config.set_live_enabled(True)
    ok, why = config.live_start_ok()
    assert not ok and "HTS" in why
    config.save_secrets({"kis_hts_id": "H"})
    ok, why = config.live_start_ok()
    assert ok


def test_set_live_enabled_toggles():
    config.save_live_config({"max_order_amount": 700000})
    config.set_live_enabled(True)
    assert config.get_live_config()["enabled"] is True
    config.set_live_enabled(False)
    assert config.get_live_config()["enabled"] is False
    # 다른 필드는 보존
    assert config.get_live_config()["max_order_amount"] == 700000


def test_start_guard_off_when_disabled():
    config.save_live_config({"enabled": False})
    ok, _ = config.live_start_ok()
    assert ok is False


def test_start_guard_real_allowed_when_enabled():
    # 무장 개념 제거 — 실전(real)도 enabled(+HTS ID)면 바로 시작 허용.
    config.set_broker("kis")  # 실전 → env real
    config.save_secrets({"kis_hts_id": "H"})
    config.save_live_config({"enabled": True})
    ok, _ = config.live_start_ok()
    assert ok is True


def test_start_guard_demo_allowed_when_enabled():
    config.set_broker("kis_demo")  # 모의투자 증권사 → env demo
    config.save_secrets({"kis_demo_hts_id": "H"})
    config.save_live_config({"enabled": True})
    ok, _ = config.live_start_ok()
    assert ok is True


# ── live-kis config 생성 ────────────────────────────────────────────────────
def _req(tmp_path):
    return RunRequest(strategy_path="strategies/RuleStrategy.py", data_folder=str(tmp_path),
                      algorithm_type="RuleStrategy", parameters={"rule_spec": "{}"})


def test_build_live_config_environment_and_handlers(tmp_path):
    cfg = build_live_config(_req(tmp_path), tmp_path / "r", "r",
                            live={"env": "demo", "max_order_amount": 0},
                            kis={"app_key": "K", "app_secret": "S", "account_no": "12345678-01"})
    assert cfg["environment"] == "live-kis"
    env = cfg["environments"]["live-kis"]
    assert env["live-mode"] is True
    assert env["live-mode-brokerage"] == "KisBrokerage"
    assert env["data-queue-handler"] == ["KisBrokerage"]
    assert env["setup-handler"].endswith("BrokerageSetupHandler")
    # 라이브는 자동종료하지 않음(킬 스위치로 종료)
    assert cfg["close-automatically"] is False


def test_build_live_config_injects_kis_brokerage_data(tmp_path):
    cfg = build_live_config(_req(tmp_path), tmp_path / "r", "r",
                            live={"env": "real", "max_order_amount": 300000, "hts_id": "H"},
                            kis={"app_key": "AK", "app_secret": "AS", "account_no": "11112222-01"},
                            token_cache="/tmp/.kis_token.json")
    assert cfg["kis-app-key"] == "AK"
    assert cfg["kis-app-secret"] == "AS"
    assert cfg["kis-account-no"] == "11112222-01"
    assert cfg["kis-env"] == "real"
    assert "kis-armed" not in cfg            # 무장 키 제거됨
    assert cfg["kis-max-order-amount"] == "300000"
    assert cfg["kis-hts-id"] == "H"
    assert cfg["kis-token-cache"] == "/tmp/.kis_token.json"
