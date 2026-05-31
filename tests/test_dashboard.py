"""대시보드(HTML) 라우트 테스트 — 가짜 runner로 LEAN 없이 검증.

흐름: ① /strategy 에서 전략+리스크 저장 → ② /backtest 에서 기간/유니버스만 정해 실행(백그라운드 잡).
"""

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from orchestrator.api import create_app
from orchestrator.lean import RunRequest, RunResult
from orchestrator.persistence import RunStore


def _wait_calls(runner, n=1, timeout=3.0):
    """백그라운드 잡이 runner를 호출할 때까지 대기(백테스트는 비동기 잡)."""
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
def isolated_config(tmp_path, monkeypatch):
    """config.local.yaml 을 임시로 격리(전략/리스크 저장이 실제 파일을 안 건드리게)."""
    from orchestrator import config
    monkeypatch.setattr(config, "CONFIG_LOCAL", tmp_path / "config.local.yaml")
    return config


@pytest.fixture
def client(tmp_path, isolated_config):
    runner = FakeRunner()
    c = TestClient(create_app(runner=runner, store=RunStore(tmp_path / "ui.db")))
    c.runner = runner  # 테스트에서 접근
    return c


def _save_default_strategy(client):
    # 조건 그룹 빌더: 그룹0=[EMA,MACD](AND), 그룹1=[RSI] → "(EMA AND MACD) OR RSI"
    from orchestrator import signals_catalog
    data = {f"{s.label}__{p.key}": str(p.default) for s in signals_catalog.CATALOG for p in s.params}
    data.update({"g0_EMA": "1", "g0_MACD": "1", "g1_RSI": "1", "period_days": "5",
                 "stop_loss": "7", "take_profit": "", "trailing": ""})
    return client.post("/strategy", data=data)


def test_index_renders(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "buylow" in r.text
    assert "백테스트" in r.text and "실행 이력" in r.text


def test_static_css_served(client):
    assert client.get("/static/style.css").status_code == 200


def test_strategy_page_renders_without_internal_tokens(client):
    r = client.get("/strategy")
    assert r.status_code == 200
    assert "EMA" in r.text and "MACD" in r.text and "리스크" in r.text
    # 서버 내부 신호값(UP/DOWN/NONE)은 사용자에게 노출하지 않는다
    assert "DOWN" not in r.text and "NONE" not in r.text


def test_strategy_save_persists_strategy_and_risk(client, isolated_config):
    r = _save_default_strategy(client)
    assert r.status_code == 200  # 303 → /strategy?saved=1 따라감
    strat = isolated_config.get_strategy()
    assert strat["rule"] == "(EMA AND MACD) OR RSI"  # 그룹 빌더가 생성한 식
    assert strat["groups"] == [["EMA", "MACD"], ["RSI"]]
    assert strat["signals"]["EMA"]["params"]["fast"] == 12  # 캐스팅(int)
    assert isolated_config.get_risk_config()["stop_loss"] == 7.0  # 같은 화면에서 리스크도 저장


def test_strategy_save_requires_a_condition(client):
    # 아무 조건도 체크 안 하면(그룹 비어있음) 저장 거부
    r = client.post("/strategy", data={"period_days": "5"}, follow_redirects=False)
    assert r.status_code == 303
    assert "error" in r.headers["location"]


def test_backtest_without_strategy_redirects(client):
    r = client.post("/backtest", data={"universe": "005930", "start": "2023-01-02",
                                       "end": "2023-12-28"}, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].startswith("/?error")  # 전략 없음 안내


def test_backtest_uses_saved_strategy(client):
    import json
    _save_default_strategy(client)
    r = client.post("/backtest", data={
        "universe": "005930", "start": "2023-01-02", "end": "2023-12-28",
        "cash": "10000000", "data_folder": "/data",
    })
    assert r.status_code == 200  # 303 → /jobs/{id}
    req = _wait_calls(client.runner)[-1]
    assert req.algorithm_type == "RuleStrategy"
    spec = json.loads(req.parameters["rule_spec"])
    assert spec["rule"] == "(EMA AND MACD) OR RSI"
    assert spec["universe"] == ["005930"]
    assert spec["start"] == "2023-01-02" and spec["cash"] == 10000000
    assert spec["signals"]["EMA"]["params"]["fast"] == 12


def test_backtest_universe_all_scans_loaded(client):
    import json
    from datetime import date
    from etl.lean_format import write_equity_daily
    from etl.sources import Bar
    import tempfile
    dd = Path(tempfile.mkdtemp())
    for t in ["005930", "000660"]:
        write_equity_daily(dd, "krx", t, [Bar(date(2023, 1, 2), 100, 100, 100, 100, 1)])
    _save_default_strategy(client)
    r = client.post("/backtest", data={
        "universe_all": "1", "data_folder": str(dd),
        "start": "2023-01-02", "end": "2023-12-28", "cash": "10000000",
    })
    assert r.status_code == 200
    spec = json.loads(_wait_calls(client.runner)[-1].parameters["rule_spec"])
    assert set(spec["universe"]) == {"005930", "000660"}


def test_run_detail_page(client):
    _save_default_strategy(client)
    client.post("/backtest", data={"universe": "005930", "start": "2023-01-02",
                                   "end": "2023-12-28", "data_folder": "/data"})
    _wait_calls(client.runner)
    r = client.get("/ui/runs/fake-run-1")
    assert r.status_code == 200
    assert "fake-run-1" in r.text and "Net Profit" in r.text
    assert client.get("/ui/runs/missing").status_code == 404


def test_load_all_triggers_background_job(client, monkeypatch):
    # 전체시장 적재 버튼 → ingest_all_market 을 백그라운드 잡으로 실행(여기선 가짜로 대체)
    import etl.universe as universe
    called = {}
    monkeypatch.setattr(universe, "ingest_all_market",
                        lambda data_dir, **kw: called.setdefault("dir", str(data_dir)))
    r = client.post("/data/load-all", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/jobs"
    deadline = time.time() + 3.0
    while time.time() < deadline and "dir" not in called:
        time.sleep(0.01)
    assert "dir" in called  # 잡이 적재 함수를 호출


def test_data_pages(tmp_path, monkeypatch):
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
    assert r.status_code == 200 and "55,500" in r.text  # 역스케일된 종가


def test_jobs_page_renders(client):
    assert client.get("/jobs").status_code == 200


def test_settings_page_and_save(tmp_path, monkeypatch):
    from orchestrator import config
    monkeypatch.setattr(config, "CONFIG_LOCAL", tmp_path / "config.local.yaml")
    for spec in config.SECRET_SPECS:
        monkeypatch.delenv(spec.env, raising=False)
    c = TestClient(create_app(runner=FakeRunner(), store=RunStore(tmp_path / "s.db")))
    assert c.get("/settings").status_code == 200
    r = c.post("/settings", data={"krx_id": "fake_id", "krx_pw": "fake_pw"})
    assert r.status_code == 200
    assert config.get_secret(config.SECRET_SPECS[0]) == "fake_id"
