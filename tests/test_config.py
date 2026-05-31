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
    config.save_risk({"stop_loss": "7", "take_profit": "", "trailing": "5", "max_drawdown": "0"})
    rc = config.get_risk_config()
    assert rc["stop_loss"] == 7.0 and rc["trailing"] == 5.0
    assert rc["take_profit"] is None and rc["max_drawdown"] is None  # 빈값/0 → 미적용


def test_apply_krx_credentials(tmp_config, monkeypatch):
    assert config.apply_krx_credentials() is False
    config.save_secrets({"krx_id": "the_id", "krx_pw": "the_pw"})
    assert config.apply_krx_credentials() is True
    import os
    assert os.environ["KRX_ID"] == "the_id"
    assert os.environ["KRX_PW"] == "the_pw"
