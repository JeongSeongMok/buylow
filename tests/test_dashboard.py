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
    """config.local.yaml 을 임시로 격리하고, 빈 데이터 폴더를 가리키게(결정적 테스트)."""
    from orchestrator import config
    monkeypatch.setattr(config, "CONFIG_LOCAL", tmp_path / "config.local.yaml")
    monkeypatch.setenv("LEAN_DATA_DIR", str(tmp_path / "data"))  # 적재 0개 상태
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


def test_root_redirects_to_strategy(client):
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 307
    assert r.headers["location"] == "/strategy"


def test_backtest_page_renders(client):
    r = client.get("/backtest")
    assert r.status_code == 200
    assert "BuyLow" in r.text
    assert "백테스트" in r.text and "실행 이력" in r.text


def test_custom_index_end_to_end(client, isolated_config):
    # 커스텀 인덱스 생성('그룹' 탭) → 데이터탭에서 '사용' → /universe/index 조회 → 삭제.
    client.post("/universe/custom", data={"name": "내그룹", "universe": "005930,000660"},
                follow_redirects=False)
    assert "내그룹" in isolated_config.get_custom_indices()
    # 적재 0건이면 loaded 비어 members 그대로 반환 + custom 플래그
    j = client.get("/universe/index/내그룹").json()
    assert j.get("custom") is True and set(j["tickers"]) == {"005930", "000660"}
    # 관리는 '그룹' 탭(/groups): 카드 + 저장된 목록
    gp = client.get("/groups").text
    assert "내 인덱스" in gp and "★ 내그룹" in gp
    # 데이터탭은 '사용'만 — 분봉적재 버튼에 노출되지만 관리 카드는 없음
    dl = client.get("/data").text
    assert "mAddIndex('내그룹'" in dl and "내 인덱스" not in dl
    # 삭제
    client.post("/universe/custom/delete", data={"key": "내그룹"}, follow_redirects=False)
    assert "내그룹" not in isolated_config.get_custom_indices()


def test_custom_index_edit_and_rename(client, isolated_config):
    client.post("/universe/custom", data={"name": "A", "universe": "005930"}, follow_redirects=False)
    # 종목 수정(같은 이름 → 덮어쓰기)
    client.post("/universe/custom", data={"name": "A", "universe": "005930,000660",
                                          "original_key": "A"}, follow_redirects=False)
    assert isolated_config.get_custom_indices()["A"]["tickers"] == ["005930", "000660"]
    # 이름 변경(rename A→B: 옛 키 삭제)
    client.post("/universe/custom", data={"name": "B", "universe": "005930,000660",
                                          "original_key": "A"}, follow_redirects=False)
    ci = isolated_config.get_custom_indices()
    assert "B" in ci and "A" not in ci


def test_index_selectors_rendered_from_ssot(client):
    # 인덱스 선택은 SSOT(etl.universe.INDEXES)에서 동적 렌더된다(데이터 탭: 분봉적재 버튼 + 적재현황 필터).
    # (백테스트 종목선택 버튼도 동일 패턴이나, 데이터 0건이면 종목 UI를 숨기므로 데이터 탭으로 검증.)
    from etl.universe import list_indices
    dl = client.get("/data").text
    for i in list_indices():
        assert f"mAddIndex('{i['key']}'" in dl     # 분봉적재 버튼
        assert f'value="{i["key"]}"' in dl         # 적재현황 인덱스 필터 옵션
    assert 'id="data-index-filter"' in dl          # 적재현황 인덱스 필터 존재


def test_first_run_prompts_data_load(client):
    # 적재 0개면 전략/백테스트 화면에 '데이터 먼저 적재' 안내 배너가 뜬다
    assert "데이터 최신화" in client.get("/strategy").text
    assert "데이터 최신화" in client.get("/backtest").text


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


def test_strategy_save_persists_intraday_execution(client, isolated_config):
    from orchestrator import signals_catalog
    data = {f"{s.label}__{p.key}": str(p.default) for s in signals_catalog.CATALOG for p in s.params}
    data.update({"g0_EMA": "1", "period_days": "5",
                 "resolution": "minute", "exec_slices": "4",
                 "exec_force_by_close": "on"})
    assert client.post("/strategy", data=data).status_code == 200
    strat = isolated_config.get_strategy()
    assert strat["resolution"] == "minute"
    # 분봉은 TWAP·장중매분·매분 고정, 사용자는 분할 수만 지정.
    assert strat["execution"]["style"] == "twap"
    assert strat["execution"]["select_eval"] == "intraday" and strat["execution"]["risk_eval"] == "bar"
    assert strat["execution"]["slices"] == 4 and strat["execution"]["force_by_close"] is True


def test_strategy_page_shows_timing_controls(client):
    t = client.get("/strategy").text
    assert "리스크 · 체결" in t and 'name="resolution"' in t and 'name="daily_fill"' in t


def test_settings_page_shows_broker_and_kis_keys(client):
    t = client.get("/settings").text
    assert "증권사" in t and "한국투자증권" in t and "KIS App Key" in t


def test_data_page_shows_minute_ingest(client):
    t = client.get("/data").text
    assert "누적 분봉 적재" in t and "KOSPI200" in t
    # 백테스트와 동일한 칩 기반 종목 추가 UX (검색 + 인덱스 일괄 + 칩)
    assert "m-ticker-search" in t and "mAddIndex" in t and "m-universe-hidden" in t
    assert "<th>분봉</th>" in t  # 적재 현황에 분봉 컬럼


def test_minute_ingest_submits_job(client):
    r = client.post("/data/minute", data={"universe": "005930, 000660", "days": "30"},
                    follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/jobs"
    # 잡은 submit 시점에 등록되므로 /jobs 목록에 즉시 나타난다(이름에 종목 수 포함).
    assert "분봉 적재" in client.get("/jobs").text


def test_minute_ingest_requires_tickers(client):
    r = client.post("/data/minute", data={"universe": "", "days": "30"},
                    follow_redirects=False)
    assert r.status_code == 303 and "error" in r.headers["location"]


def test_data_page_shows_auto_schedule(client):
    t = client.get("/data").text
    # 자동 적재 스케줄 카드 + 분봉 자동적재 저장 버튼
    assert "자동 적재 스케줄" in t and "자동적재 대상으로 저장" in t


def test_save_schedule_minute_universe(client, isolated_config):
    r = client.post("/data/schedule/minute", data={"universe": "005930, 000660"},
                    follow_redirects=False)
    assert r.status_code == 303 and "saved=schedule" in r.headers["location"]
    assert isolated_config.get_scheduler_config()["minute_universe"] == ["005930", "000660"]
    # 빈 선택은 분봉 자동적재를 끄는 의미(저장됨)
    r = client.post("/data/schedule/minute", data={"universe": ""}, follow_redirects=False)
    assert r.status_code == 303
    assert isolated_config.get_scheduler_config()["minute_universe"] == []


def _form(pairs):
    from starlette.datastructures import FormData
    return FormData(pairs)


def test_resolve_minute_tickers_index_ok(monkeypatch):
    from orchestrator.dashboard import routes
    from etl import universe
    monkeypatch.setattr(universe, "list_universe", lambda key, on=None: ["005930", "000660"])
    out, errors = routes._resolve_minute_tickers(_form([("index", "KOSPI200")]))
    assert out == ["005930", "000660"] and errors == []


def test_resolve_minute_tickers_index_failure_surfaced(monkeypatch):
    from orchestrator.dashboard import routes
    from etl import universe
    def boom(key, on=None):
        raise RuntimeError("login required")
    monkeypatch.setattr(universe, "list_universe", boom)
    out, errors = routes._resolve_minute_tickers(_form([("index", "KOSPI200")]))
    assert out == [] and errors and "KOSPI200" in errors[0]


def test_resolve_minute_tickers_manual_plus_index_dedupes(monkeypatch):
    from orchestrator.dashboard import routes
    from etl import universe
    monkeypatch.setattr(universe, "list_universe", lambda key, on=None: ["005930", "035720"])
    out, _ = routes._resolve_minute_tickers(
        _form([("universe", "005930, 000660"), ("index", "KOSDAQ150")]))
    assert out == ["005930", "000660", "035720"]  # 직접입력 먼저 + 중복(005930) 제거


def test_settings_save_broker_and_kis_secret(client, isolated_config):
    r = client.post("/settings", data={"broker": "kis", "kis_app_key": "MYKEY"})
    assert r.status_code == 200
    assert isolated_config.get_broker() == "kis"
    assert isolated_config.get_kis_credentials()["app_key"] == "MYKEY"


def test_settings_page_two_columns_and_no_storage_wording(client):
    t = client.get("/settings").text
    assert "한국거래소" in t and "증권사" in t          # 2개 컬럼
    assert "연동 테스트" in t                            # 테스트 버튼
    assert "toggle-pw" in t and "togglePw" in t          # 표준 password show/hide 토글
    assert "저장소에는 올라가지 않습니다" not in t        # 삭제된 워딩


def test_settings_test_krx_requires_creds(client):
    # 자격증명 없으면 네트워크 호출 없이 ok:false 즉시 반환
    d = client.post("/settings/test/krx").json()
    assert d["ok"] is False


def test_settings_test_kis_requires_creds(client):
    d = client.post("/settings/test/kis").json()
    assert d["ok"] is False


def test_strategy_save_requires_a_condition(client):
    # 아무 조건도 체크 안 하면(그룹 비어있음) 저장 거부
    r = client.post("/strategy", data={"period_days": "5"}, follow_redirects=False)
    assert r.status_code == 303
    assert "error" in r.headers["location"]


def test_backtest_without_strategy_redirects(client):
    r = client.post("/backtest", data={"universe": "005930", "start": "2023-01-02",
                                       "end": "2023-12-28"}, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].startswith("/backtest?error")  # 전략 없음 안내


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
    assert spec["start"] == "2023-01-02" and spec["cash"] == 100000000  # 1억 고정
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
    assert "백테스트 결과" in r.text and "총 수익률" in r.text  # 한국어 친화 요약
    assert "Net Profit" in r.text  # 원본 통계는 접힌 영역에 그대로
    assert client.get("/ui/runs/missing").status_code == 404


def test_run_delete_removes_record(client):
    _save_default_strategy(client)
    client.post("/backtest", data={"universe": "005930", "start": "2023-01-02",
                                   "end": "2023-12-28", "data_folder": "/data"})
    _wait_calls(client.runner)
    assert client.get("/ui/runs/fake-run-1").status_code == 200
    r = client.post("/ui/runs/fake-run-1/delete", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/backtest"
    assert client.get("/ui/runs/fake-run-1").status_code == 404


def test_runs_clear_empties_history(client):
    _save_default_strategy(client)
    client.post("/backtest", data={"universe": "005930", "start": "2023-01-02",
                                   "end": "2023-12-28", "data_folder": "/data"})
    _wait_calls(client.runner)
    r = client.post("/ui/runs/clear", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/backtest"
    assert "아직 실행 이력이 없습니다" in client.get("/backtest").text


def test_run_trades_pagination(tmp_path, monkeypatch):
    import json
    from orchestrator import config
    monkeypatch.setattr(config, "CONFIG_LOCAL", tmp_path / "config.local.yaml")
    monkeypatch.setenv("LEAN_DATA_DIR", str(tmp_path / "data"))
    # 250건 체결을 가진 결과 JSON을 run_dir 에 만든다(캐시는 run_dir/trades.jsonl)
    rundir = tmp_path / "runs" / "rid"
    rundir.mkdir(parents=True)
    orders = {str(i): {"status": 3, "quantity": (1 if i % 2 == 0 else -1), "price": 100,
                       "value": 100, "lastFillTime": f"2026-01-{(i % 28) + 1:02d}T00:00:00Z",
                       "symbol": {"value": "005930"}} for i in range(250)}
    rj = rundir / "rid.json"
    rj.write_text(json.dumps({"orders": orders}), encoding="utf-8")
    store = RunStore(tmp_path / "s.db")
    store.save_run({"run_id": "rid", "strategy": "s.py", "algorithm_type": "S",
                    "data_folder": "/data", "parameters": {}, "exit_code": 0, "success": True,
                    "statistics": {}, "run_dir": str(rundir), "log_path": str(rundir / "run.log"),
                    "result_json": str(rj)})
    c = TestClient(create_app(runner=FakeRunner(), store=store))

    # 첫 페이지: 다음만, 이전 없음 + 전체 건수 표시 + 캐시 파일 생성
    r = c.get("/ui/runs/rid/trades?offset=0&limit=100")
    assert r.status_code == 200
    assert "250건" in r.text
    assert "다음" in r.text and "◀ 이전" not in r.text
    assert (rundir / "trades.jsonl").exists()  # '한 뎁스' 캐시가 생성됨
    assert r.text.count("<tr>") == 1 + 100  # 헤더 1 + 100행

    # 마지막 페이지(offset 200): 이전만, 다음 없음, 50행
    r2 = c.get("/ui/runs/rid/trades?offset=200&limit=100")
    assert "◀ 이전" in r2.text and "다음 ▶" not in r2.text
    assert r2.text.count("<tr>") == 1 + 50


def test_run_detail_loads_trades_lazily(client):
    # 상세 페이지는 거래를 인라인으로 싣지 않고 HTMX로 가져온다(대량 거래 성능).
    _save_default_strategy(client)
    client.post("/backtest", data={"universe": "005930", "start": "2023-01-02",
                                   "end": "2023-12-28", "data_folder": "/data"})
    _wait_calls(client.runner)
    t = client.get("/ui/runs/fake-run-1").text
    assert "/ui/runs/fake-run-1/trades" in t and 'hx-trigger="load"' in t
    assert "삭제" in t  # 상세 페이지에 삭제 버튼


def test_format_won_korean():
    from orchestrator.dashboard.routes import format_won
    assert format_won(147000257) == "1억 4,700만원"
    assert format_won(100000000) == "1억원"
    assert format_won(2339943) == "234만원"
    assert format_won(5000) == "5,000원"
    assert format_won(-4700000) == "-470만원"


def test_parse_orders(tmp_path):
    import json
    from orchestrator.dashboard.routes import parse_orders
    assert parse_orders(None) == []
    rj = tmp_path / "r.json"
    rj.write_text(json.dumps({"orders": {
        "1": {"status": 3, "quantity": 10, "price": 80000, "value": 800000, "tag": "",
              "lastFillTime": "2026-03-10T04:00:00Z", "symbol": {"value": "005930"}},
        "2": {"status": 3, "quantity": -10, "price": 90000, "value": -900000, "tag": "Stop Loss",
              "lastFillTime": "2026-03-12T04:00:00Z", "symbol": {"value": "005930"}},
        "3": {"status": 5, "quantity": 5, "price": 1, "value": 5, "symbol": {"value": "X"}},  # 미체결
    }}), encoding="utf-8")
    rows = parse_orders(str(rj))
    assert len(rows) == 2  # 체결(3)만, 미체결(5) 제외
    assert rows[0]["side"] == "매수" and rows[0]["time"] == "2026-03-10" and rows[0]["ticker"] == "005930"
    assert rows[1]["side"] == "매도" and rows[1]["reason"] == "Stop Loss"  # 태그 있으면 그대로


def test_parse_rule_reasons_and_merge(tmp_path):
    import json
    from orchestrator.dashboard.routes import parse_rule_reasons, parse_orders
    (tmp_path / "x-log.txt").write_text(
        "20260310 ...\nRULEHIT 2026-03-10 005930 BUY EMA+FLOW\nRULEHIT 2026-03-12 005930 SELL FLOW\n",
        encoding="utf-8")
    reasons = parse_rule_reasons(str(tmp_path))
    assert reasons[("2026-03-10", "005930", "BUY")] == "EMA+FLOW"
    # 거래 내역에 사유로 병합 (리스크 태그 없을 때 RULEHIT 사유 사용)
    rj = tmp_path / "r.json"
    rj.write_text(json.dumps({"orders": {
        "1": {"status": 3, "quantity": 10, "price": 1, "value": 10, "tag": "",
              "lastFillTime": "2026-03-10T04:00:00Z", "symbol": {"value": "005930"}}}}), encoding="utf-8")
    rows = parse_orders(str(rj), reasons)
    assert rows[0]["reason"] == "EMA+FLOW"


def test_trade_history_shows_stock_name(tmp_path):
    import json
    from etl.names import names_csv_path, load_names
    from orchestrator.dashboard.routes import parse_orders
    p = names_csv_path(tmp_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("005930,삼성전자\n000660,SK하이닉스\n", encoding="utf-8")
    names = load_names(tmp_path)
    assert names["005930"] == "삼성전자"
    rj = tmp_path / "r.json"
    rj.write_text(json.dumps({"orders": {"1": {"status": 3, "quantity": 1, "price": 1, "value": 1,
                  "symbol": {"value": "005930"}, "lastFillTime": "2026-01-02T00:00:00Z"}}}), encoding="utf-8")
    rows = parse_orders(str(rj), None, names)
    assert rows[0]["name"] == "삼성전자" and rows[0]["ticker"] == "005930"


def test_friendly_stats_korean_labels():
    from orchestrator.dashboard.routes import friendly_stats
    rows = friendly_stats({
        "Net Profit": "47.000%", "Start Equity": "100000000", "End Equity": "147000257",
        "Total Orders": "27", "Win Rate": "46%", "Total Fees": "KRW2339943.00",
        "Sharpe Ratio": "2.131",
    })
    by = {r["label"]: r["value"] for r in rows}
    assert by["총 수익률"] == "47%"
    assert by["최종 자산"] == "1억 4,700만원"
    assert by["순손익"] == "+4,700만원"
    assert by["총 거래 횟수"] == "27회"
    assert by["총 수수료"] == "234만원"
    assert by["샤프 지수"] == "2.13"


def test_update_data_triggers_job(client, monkeypatch):
    # '데이터 최신화' → update_all_market 을 백그라운드 잡으로 실행(여기선 가짜로 대체)
    import etl.universe as universe
    called = {}
    def fake(data_dir, **kw):
        called["dir"] = str(data_dir)
        return {"price_tickers": 0, "flow_ok": 0, "fund_ok": 0, "trading_days": 0}
    monkeypatch.setattr(universe, "update_all_market", fake)
    r = client.post("/data/update", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/jobs"
    deadline = time.time() + 3.0
    while time.time() < deadline and "dir" not in called:
        time.sleep(0.01)
    assert "dir" in called  # 잡이 최신화 함수를 호출


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


def test_data_detail_shows_minute_bars(tmp_path, monkeypatch):
    from datetime import date
    from orchestrator import config
    from etl.lean_format import write_equity_minute
    from etl.sources import MinuteBar
    monkeypatch.setattr(config, "CONFIG_LOCAL", tmp_path / "config.local.yaml")
    monkeypatch.setenv("LEAN_DATA_DIR", str(tmp_path / "data"))
    write_equity_minute(tmp_path / "data", "krx", "005930", date(2026, 6, 1),
                        [MinuteBar(9 * 3600 * 1000, 71000, 71500, 70900, 71200, 1234)])
    c = TestClient(create_app(runner=FakeRunner(), store=RunStore(tmp_path / "d.db")))
    r = c.get("/data/005930")
    assert r.status_code == 200
    assert "2026-06-01" in r.text and "09:00" in r.text  # 날짜 선택 + 분봉 시각
    assert "71,200" in r.text                            # 분봉 종가


def test_universe_index_returns_loaded_constituents(client, monkeypatch):
    import etl.universe as u
    import etl.catalog as cat
    monkeypatch.setattr(u, "index_members_cached",
                        lambda market, data_dir=None, **k: ["005930", "000660", "999999"])
    monkeypatch.setattr(cat, "list_price_tickers", lambda d: ["005930", "000660"])  # 적재된 것만
    data = client.get("/universe/index/KOSPI200").json()
    assert data["tickers"] == ["005930", "000660"]  # 교집합(멤버 순서)
    assert data["total"] == 3 and data["available"] == 2


def test_universe_index_unknown_name(client):
    data = client.get("/universe/index/NASDAQ").json()
    assert data["error"] and data["tickers"] == []


def test_universe_index_krx_failure_is_graceful(client, monkeypatch):
    import etl.universe as u
    def boom(market, data_dir=None, **k): raise RuntimeError("login needed")
    monkeypatch.setattr(u, "index_members_cached", boom)
    data = client.get("/universe/index/KOSDAQ150").json()
    assert "조회 실패" in data["error"] and data["tickers"] == []


def test_jobs_page_renders(client):
    assert client.get("/jobs").status_code == 200


def test_parse_progress_picks_last():
    from orchestrator.dashboard.routes import _parse_progress
    lines = ["...", "20230101 PROGRESS 10% 2023-01-01", "noise",
             "20230601 PROGRESS 50% 2023-06-01"]
    assert _parse_progress(lines) == 50
    assert _parse_progress(["no progress here"]) is None


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
