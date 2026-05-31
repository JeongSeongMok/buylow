"""대시보드(HTML) 라우트 테스트 — 가짜 runner로 LEAN 없이 검증."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from orchestrator.api import create_app
from orchestrator.lean import RunRequest, RunResult
from orchestrator.persistence import RunStore


def _wait_calls(runner, n=1, timeout=3.0):
    """백그라운드 잡이 runner를 호출할 때까지 대기(대시보드 백테스트는 비동기)."""
    import time
    deadline = time.time() + timeout
    while time.time() < deadline and len(runner.calls) < n:
        time.sleep(0.01)
    return runner.calls


class FakeRunner:
    def __init__(self):
        self.calls = []

    def run_backtest(self, request: RunRequest, on_start=None) -> RunResult:
        self.calls.append(request)
        if on_start:
            on_start("fake-run-1", "/tmp/fake-run-1/run.log")
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
    assert "백테스트 결과" in r.text  # ② 백테스트 챕터(결과/이력)


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


def test_run_uses_config_default_data_folder(client, tmp_path, monkeypatch):
    # data_folder 미지정이어도 config 기본값으로 해석돼 실행된다
    from orchestrator import config
    cfg = tmp_path / "config.local.yaml"
    cfg.write_text("data_folder: /cfg/data\n")
    monkeypatch.setattr(config, "CONFIG_LOCAL", cfg)
    monkeypatch.delenv("LEAN_DATA_DIR", raising=False)
    r = client.post("/ui/runs", data={"strategy": "strategies/SmokeTestAlgorithm.py"})
    assert r.status_code == 200
    assert "SmokeTestAlgorithm" in r.text  # 실행되어 이력에 표시됨

def test_run_detail_page(client):
    client.post("/ui/runs", data={"strategy": "strategies/SmokeTestAlgorithm.py", "data_folder": "/data"})
    r = client.get("/ui/runs/fake-run-1")
    assert r.status_code == 200
    assert "fake-run-1" in r.text
    assert "Net Profit" in r.text
    assert client.get("/ui/runs/missing").status_code == 404


def test_data_pages(tmp_path, monkeypatch):
    # config의 data_folder를 샘플 적재한 tmp로 지정 → /data, /data/{ticker} 렌더
    from datetime import date
    from orchestrator import config
    from etl.lean_format import write_equity_daily
    from etl.sources import Bar
    monkeypatch.setattr(config, "CONFIG_LOCAL", tmp_path / "config.local.yaml")
    monkeypatch.setenv("LEAN_DATA_DIR", str(tmp_path / "data"))
    write_equity_daily(tmp_path / "data", "krx", "005930",
                       [Bar(date(2023, 1, 2), 55500, 56100, 55200, 55500, 10031448)])

    c = TestClient(create_app(runner=FakeRunner(), store=RunStore(tmp_path / "d.db")))
    r = c.get("/data")
    assert r.status_code == 200 and "005930" in r.text
    r = c.get("/data/005930")
    assert r.status_code == 200 and "55,500" in r.text  # 역스케일된 종가 표시


def test_jobs_page_renders(client):
    assert client.get("/jobs").status_code == 200


def test_rules_page_renders(tmp_path):
    c = TestClient(create_app(runner=FakeRunner(), store=RunStore(tmp_path / "r.db")))
    r = c.get("/rules")
    assert r.status_code == 200
    assert "규칙" in r.text and "EMA" in r.text and "MACD" in r.text


def test_rules_run_builds_rule_spec(tmp_path):
    import json
    runner = FakeRunner()
    c = TestClient(create_app(runner=runner, store=RunStore(tmp_path / "r.db")))
    r = c.post("/rules", data={
        "EMA__fast": "10", "EMA__slow": "30",
        "MACD__fast": "12", "MACD__slow": "26", "MACD__signal": "9",
        "RSI__period": "14", "RSI__oversold": "30", "RSI__overbought": "70",
        "MOM__lookback": "60",
        "rule": "(EMA AND MACD) OR RSI",
        "period_days": "5", "universe": "005930",
        "start": "2023-01-02", "end": "2023-12-28", "cash": "10000000", "data_folder": "/data",
    })
    assert r.status_code == 200
    req = _wait_calls(runner)[-1]
    assert req.algorithm_type == "RuleStrategy"
    spec = json.loads(req.parameters["rule_spec"])
    assert spec["rule"] == "(EMA AND MACD) OR RSI"
    assert spec["signals"]["EMA"]["params"]["fast"] == 10  # 캐스팅(int)


def test_rules_universe_all_scans_loaded(tmp_path):
    # '적재된 전체 종목' 체크 시 universe = ./data 의 가격 적재 종목 전부
    import json
    from datetime import date
    from etl.lean_format import write_equity_daily
    from etl.sources import Bar
    dd = tmp_path / "data"
    for t in ["005930", "000660"]:
        write_equity_daily(dd, "krx", t, [Bar(date(2023, 1, 2), 100, 100, 100, 100, 1)])
    runner = FakeRunner()
    c = TestClient(create_app(runner=runner, store=RunStore(tmp_path / "r.db")))
    r = c.post("/rules", data={
        "rule": "EMA", "EMA__fast": "12", "EMA__slow": "26",
        "universe_all": "1", "data_folder": str(dd),
        "start": "2023-01-02", "end": "2023-12-28", "cash": "10000000",
    })
    assert r.status_code == 200
    spec = json.loads(_wait_calls(runner)[-1].parameters["rule_spec"])
    assert set(spec["universe"]) == {"005930", "000660"}


def test_rules_run_rejects_bad_expression(tmp_path):
    c = TestClient(create_app(runner=FakeRunner(), store=RunStore(tmp_path / "r.db")))
    r = c.post("/rules", data={"rule": "(EMA AND", "universe": "005930",
                               "start": "2023-01-02", "end": "2023-12-28"}, follow_redirects=False)
    assert r.status_code == 303
    assert "error" in r.headers["location"]  # 규칙식 오류로 리다이렉트


def test_compose_page_lists_catalog(tmp_path):
    c = TestClient(create_app(runner=FakeRunner(), store=RunStore(tmp_path / "c.db")))
    r = c.get("/compose")
    assert r.status_code == 200
    assert "EMA 교차" in r.text and "MACD" in r.text  # LEAN 내장 카탈로그 노출


def test_compose_runs_composition(tmp_path):
    import json
    runner = FakeRunner()
    c = TestClient(create_app(runner=runner, store=RunStore(tmp_path / "c.db")))
    r = c.post("/compose", data={
        "alpha": ["ema_cross", "rsi"],
        "ema_cross__fast": "10", "ema_cross__slow": "40",
        "rsi__period": "14",
        "universe": "005930", "start": "2023-01-02", "end": "2023-12-28",
        "cash": "10000000", "data_folder": "/data",
    })
    assert r.status_code == 200  # 리다이렉트 따라가 run 상세
    req = _wait_calls(runner)[-1]
    assert req.algorithm_type == "Composed"
    comp = json.loads(req.parameters["composition"])
    assert {a["name"] for a in comp["alphas"]} == {"ema_cross", "rsi"}
    assert comp["alphas"][0]["params"]["fast"] == 10  # 타입 캐스팅(int)


def test_settings_page_and_save(tmp_path, monkeypatch):
    # 실제 config.local.yaml/환경변수를 건드리지 않도록 격리
    from orchestrator import config
    monkeypatch.setattr(config, "CONFIG_LOCAL", tmp_path / "config.local.yaml")
    for spec in config.SECRET_SPECS:
        monkeypatch.delenv(spec.env, raising=False)

    c = TestClient(create_app(runner=FakeRunner(), store=RunStore(tmp_path / "s.db")))
    assert c.get("/settings").status_code == 200
    # 저장(폼) → 303 리다이렉트 따라가 200, 그리고 config에 반영
    r = c.post("/settings", data={"krx_id": "fake_id", "krx_pw": "fake_pw"})
    assert r.status_code == 200
    assert config.get_secret(config.SECRET_SPECS[0]) == "fake_id"
