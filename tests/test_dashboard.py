"""대시보드(HTML) 라우트 테스트 — 가짜 runner로 LEAN 없이 검증."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from orchestrator.api import create_app
from orchestrator.lean import RunRequest, RunResult
from orchestrator.persistence import RunStore


class FakeRunner:
    def run_backtest(self, request: RunRequest) -> RunResult:
        return RunResult(
            run_id="fake-run-1",
            exit_code=0,
            statistics={"Total Orders": "1", "Net Profit": "1.694%"},
            run_dir=Path("/runs/fake-run-1"),
            log_path=Path("/runs/fake-run-1/run.log"),
            result_json=Path("/runs/fake-run-1/fake-run-1-summary.json"),
        )


@pytest.fixture
def client(tmp_path):
    return TestClient(create_app(runner=FakeRunner(), store=RunStore(tmp_path / "ui.db")))


def test_index_renders(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "buylow" in r.text
    assert "새 백테스트" in r.text
    # strategies/ 의 전략이 옵션으로 노출돼야 함
    assert "SmokeTestAlgorithm" in r.text


def test_static_css_served(client):
    assert client.get("/static/style.css").status_code == 200


def test_run_backtest_returns_updated_table(client):
    r = client.post("/ui/runs", data={
        "strategy": "strategies/SmokeTestAlgorithm.py",
        "data_folder": "/data",
    })
    assert r.status_code == 200
    # 갱신된 실행 이력 partial에 방금 실행이 보여야 함
    assert "SmokeTestAlgorithm" in r.text
    assert "1.694%" in r.text
    assert 'id="runs-table"' in r.text


def test_run_requires_data_folder(client, monkeypatch):
    monkeypatch.delenv("LEAN_DATA_DIR", raising=False)
    r = client.post("/ui/runs", data={"strategy": "strategies/SmokeTestAlgorithm.py"})
    assert r.status_code == 200
    assert "데이터 폴더" in r.text  # 에러 메시지

def test_run_detail_page(client):
    client.post("/ui/runs", data={"strategy": "strategies/SmokeTestAlgorithm.py", "data_folder": "/data"})
    r = client.get("/ui/runs/fake-run-1")
    assert r.status_code == 200
    assert "fake-run-1" in r.text
    assert "Net Profit" in r.text
    assert client.get("/ui/runs/missing").status_code == 404
