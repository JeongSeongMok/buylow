"""테스트 공통 격리.

모든 테스트가 개발자의 실제 config.local.yaml(전략/리스크/시크릿)이나 환경변수를 읽지 않도록
config.CONFIG_LOCAL을 임시 파일로, 관련 환경변수를 비워둔다. 개별 테스트가 직접 monkeypatch로
다시 설정하면 그 값이 우선한다(autouse가 먼저 적용되므로).
"""

import pytest


@pytest.fixture(autouse=True)
def _isolate_config(tmp_path_factory, monkeypatch):
    from orchestrator import config
    monkeypatch.setattr(config, "CONFIG_LOCAL", tmp_path_factory.mktemp("cfg") / "config.local.yaml")
    for spec in config.SECRET_SPECS:
        monkeypatch.delenv(spec.env, raising=False)
    monkeypatch.delenv("LEAN_DATA_DIR", raising=False)
    monkeypatch.delenv("BUYLOW_DASHBOARD_PORT", raising=False)
