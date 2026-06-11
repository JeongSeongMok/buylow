"""config/시크릿 레이어 단위 테스트 — 임시 config 파일 + 가짜 값(실제 키 미사용)."""

import pytest

from orchestrator import config


@pytest.fixture
def tmp_config(tmp_path, monkeypatch):
    # 실제 config.local.yaml/환경변수가 테스트에 새지 않도록 격리
    monkeypatch.setattr(config, "CONFIG_LOCAL", tmp_path / "config.local.yaml")
    for spec in config.SECRET_SPECS:
        monkeypatch.delenv(spec.env, raising=False)
    monkeypatch.delenv("LEAN_DATA_DIR", raising=False)
    monkeypatch.delenv("BUYLOW_DASHBOARD_PORT", raising=False)
    return tmp_path


def test_data_folder_default(tmp_config):
    assert config.get_data_folder() == str(config.DEFAULT_DATA_FOLDER)


def test_env_overrides_file(tmp_config, monkeypatch):
    (tmp_config / "config.local.yaml").write_text("data_folder: /from/file\n")
    assert config.get_data_folder() == "/from/file"
    monkeypatch.setenv("LEAN_DATA_DIR", "/from/env")
    assert config.get_data_folder() == "/from/env"  # env 우선


def test_dashboard_port_resolution(tmp_config, monkeypatch):
    assert config.get_dashboard_port() == config.DEFAULT_DASHBOARD_PORT
    (tmp_config / "config.local.yaml").write_text("dashboard_port: 9001\n")
    assert config.get_dashboard_port() == 9001
    monkeypatch.setenv("BUYLOW_DASHBOARD_PORT", "9999")
    assert config.get_dashboard_port() == 9999


def test_secret_env_then_file(tmp_config, monkeypatch):
    krx_id = config.SECRET_SPECS[0]
    assert config.get_secret(krx_id) is None              # 없음
    config.save_secrets({"krx_id": "file_id"})
    assert config.get_secret(krx_id) == "file_id"          # 파일
    monkeypatch.setenv("KRX_ID", "env_id")
    assert config.get_secret(krx_id) == "env_id"           # env 우선


def test_save_secrets_filters_invalid_and_empty(tmp_config):
    config.save_secrets({"krx_id": "x", "krx_pw": "", "bogus": "y"})
    assert config.get_secret(config.SECRET_SPECS[0]) == "x"   # krx_id 저장
    assert config.get_secret(config.SECRET_SPECS[1]) is None  # 빈 krx_pw 무시
    # 미정의 키는 저장 안 됨
    assert "bogus" not in (config._load_local().get("secrets") or {})


def test_missing_and_status(tmp_config):
    assert len(config.missing_secrets()) == len(config.SECRET_SPECS)
    config.save_secrets({"krx_id": "x", "krx_pw": "y"})
    assert config.missing_secrets() == []
    assert all(s["set"] for s in config.secret_status())


def test_risk_save_and_get(tmp_config):
    assert config.get_risk_config()["stop_loss"] is None  # 기본 미적용
    config.save_risk({"stop_loss": "7", "take_profit": "0", "trailing": "5"})
    rc = config.get_risk_config()
    assert rc["stop_loss"] == 7.0 and rc["trailing"] == 5.0
    assert rc["take_profit"] is None  # 0 → 미적용
    assert "max_drawdown" not in rc  # 포트폴리오 손실한도 기능 제거됨


def test_risk_form_values_always_filled(tmp_config):
    # 저장 이력 없으면 기본값
    assert config.risk_form_values() == config.DEFAULT_RISK
    # 저장값이 있으면 그 값, 비운(미적용) 항목은 폼엔 기본값으로 채워 보인다
    config.save_risk({"stop_loss": "10", "take_profit": "", "trailing": "5"})
    fv = config.risk_form_values()
    assert fv["stop_loss"] == 10.0 and fv["trailing"] == 5.0
    assert fv["take_profit"] == config.DEFAULT_RISK["take_profit"]  # placeholder 아닌 실제 기본값


def test_strategy_save_and_get(tmp_config):
    assert config.get_strategy() is None  # 기본 없음
    spec = {"signals": {"EMA": {"type": "ema", "params": {"fast": 5, "slow": 20}}},
            "rule": "EMA", "period_days": 3}
    config.save_strategy(spec)
    got = config.get_strategy()
    assert got["rule"] == "EMA" and got["period_days"] == 3
    assert got["signals"]["EMA"]["params"]["fast"] == 5
    # 단일 전략 — 다시 저장하면 덮어쓴다
    config.save_strategy({"signals": {}, "rule": "MACD", "period_days": 7})
    assert config.get_strategy()["rule"] == "MACD"


def test_broker_default_and_set(tmp_config, monkeypatch):
    assert config.get_broker() == config.DEFAULT_BROKER  # 기본 kis
    config.set_broker("toss")
    assert config.get_broker() == "toss"
    monkeypatch.setenv("BUYLOW_BROKER", "kis")
    assert config.get_broker() == "kis"  # env 우선
    monkeypatch.delenv("BUYLOW_BROKER")
    # 잘못된 값은 거부 / 기본으로 폴백
    with pytest.raises(ValueError):
        config.set_broker("nh")
    (tmp_config / "config.local.yaml").write_text("broker: garbage\n")
    assert config.get_broker() == config.DEFAULT_BROKER


def test_kis_secrets_and_credentials(tmp_config, monkeypatch):
    # 기본 미설정
    cred = config.get_kis_credentials()
    assert cred == {"app_key": None, "app_secret": None, "account_no": None}
    # 브로커 시크릿도 save_secrets 화이트리스트에 포함
    config.save_secrets({"kis_app_key": "AK", "kis_app_secret": "SK",
                         "kis_account_no": "12345678-01"})
    cred = config.get_kis_credentials()
    assert cred["app_key"] == "AK" and cred["app_secret"] == "SK"
    assert cred["account_no"] == "12345678-01"
    # env 우선
    monkeypatch.setenv("BUYLOW_KIS_APP_KEY", "env_key")
    assert config.get_kis_credentials()["app_key"] == "env_key"
    # pykrx 시크릿 게이트(missing_secrets)는 브로커 시크릿과 무관 — 여전히 krx만 본다
    assert {s.key for s in config.missing_secrets()} == {"krx_id", "krx_pw"}


def test_broker_secret_status(tmp_config):
    st = config.broker_secret_status("kis")
    assert {s["key"] for s in st} == {"kis_app_key", "kis_app_secret", "kis_account_no", "kis_hts_id"}
    assert all(not s["set"] for s in st)
    config.save_secrets({"kis_app_key": "AK"})
    st = {s["key"]: s["set"] for s in config.broker_secret_status("kis")}
    assert st["kis_app_key"] and not st["kis_app_secret"]


def test_scheduler_config_defaults(tmp_config):
    # 기본: 켜짐, 30분 간격, 분봉 대상 없음
    cfg = config.get_scheduler_config()
    assert cfg["enabled"] is True
    assert cfg["interval_minutes"] == 30
    assert cfg["minute_universe"] == []


def test_scheduler_minute_universe_save(tmp_config):
    config.save_scheduler_minute_universe(["005930", "000660", "005930", " ", "035720"])
    # 중복/공백 제거 + 순서 보존
    assert config.get_scheduler_config()["minute_universe"] == ["005930", "000660", "035720"]
    # 빈 리스트면 분봉 자동적재 끔
    config.save_scheduler_minute_universe([])
    assert config.get_scheduler_config()["minute_universe"] == []


def test_apply_krx_credentials(tmp_config, monkeypatch):
    assert config.apply_krx_credentials() is False
    config.save_secrets({"krx_id": "the_id", "krx_pw": "the_pw"})
    assert config.apply_krx_credentials() is True
    import os
    assert os.environ["KRX_ID"] == "the_id"
    assert os.environ["KRX_PW"] == "the_pw"
