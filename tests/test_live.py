"""라이브(실주문) 설정 + live-kis config 생성 단위 테스트 (LEAN/.NET·네트워크 없이).

실주문 경로의 안전장치(무장 가드·환경 분기·주문한도·KIS 자격증명 주입)를 검증한다.
conftest의 _isolate_config가 config.local.yaml을 테스트마다 임시파일로 격리한다.
"""

from orchestrator import config
from orchestrator.lean.runner import RunRequest, build_live_config


# ── 라이브 설정 ─────────────────────────────────────────────────────────────
def test_live_config_defaults_are_safe():
    lc = config.get_live_config()
    assert lc["enabled"] is False      # 기본 꺼짐
    assert lc["armed"] is False        # 기본 미무장
    assert lc["env"] == "demo"         # 기본 모의
    assert lc["max_order_amount"] == 0


def test_save_and_get_live_config():
    config.save_live_config({"enabled": True, "armed": True, "env": "real",
                             "max_order_amount": 500000, "hts_id": "myhts"})
    lc = config.get_live_config()
    assert lc["enabled"] and lc["armed"]
    assert lc["env"] == "real"
    assert lc["max_order_amount"] == 500000
    assert lc["hts_id"] == "myhts"


def test_save_live_config_invalid_env_falls_back_demo():
    config.save_live_config({"env": "bogus"})
    assert config.get_live_config()["env"] == "demo"


def test_set_live_enabled_and_armed_toggles():
    config.set_live_enabled(True)
    assert config.get_live_config()["enabled"] is True
    config.set_live_armed(True)
    assert config.get_live_config()["armed"] is True
    config.set_live_enabled(False)
    assert config.get_live_config()["enabled"] is False
    # 다른 필드는 보존
    assert config.get_live_config()["armed"] is True


def test_arming_guard_off_when_disabled():
    config.save_live_config({"enabled": False})
    ok, _ = config.live_arming_ok()
    assert ok is False


def test_arming_guard_real_requires_armed():
    config.save_live_config({"enabled": True, "armed": False, "env": "real"})
    ok, why = config.live_arming_ok()
    assert ok is False and "무장" in why
    config.save_live_config({"armed": True})
    ok, _ = config.live_arming_ok()
    assert ok is True


def test_arming_guard_demo_allowed_without_arming():
    config.save_live_config({"enabled": True, "armed": False, "env": "demo"})
    ok, _ = config.live_arming_ok()
    assert ok is True  # 모의는 무장 없이도 시작 허용


# ── live-kis config 생성 ────────────────────────────────────────────────────
def _req(tmp_path):
    return RunRequest(strategy_path="strategies/RuleStrategy.py", data_folder=str(tmp_path),
                      algorithm_type="RuleStrategy", parameters={"rule_spec": "{}"})


def test_build_live_config_environment_and_handlers(tmp_path):
    cfg = build_live_config(_req(tmp_path), tmp_path / "r", "r",
                            live={"env": "demo", "armed": False, "max_order_amount": 0},
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
                            live={"env": "real", "armed": True, "max_order_amount": 300000, "hts_id": "H"},
                            kis={"app_key": "AK", "app_secret": "AS", "account_no": "11112222-01"},
                            token_cache="/tmp/.kis_token.json")
    assert cfg["kis-app-key"] == "AK"
    assert cfg["kis-app-secret"] == "AS"
    assert cfg["kis-account-no"] == "11112222-01"
    assert cfg["kis-env"] == "real"
    assert cfg["kis-armed"] == "true"        # 무장 → 문자열 true
    assert cfg["kis-max-order-amount"] == "300000"
    assert cfg["kis-hts-id"] == "H"
    assert cfg["kis-token-cache"] == "/tmp/.kis_token.json"


def test_build_live_config_unarmed_is_false_string(tmp_path):
    cfg = build_live_config(_req(tmp_path), tmp_path / "r", "r",
                            live={"env": "demo", "armed": False, "max_order_amount": 0},
                            kis={"app_key": "K", "app_secret": "S", "account_no": "1-01"})
    assert cfg["kis-armed"] == "false"       # 미무장 → 어댑터가 드라이런 거부
