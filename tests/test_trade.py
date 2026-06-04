"""매매 탭 테스트 — 거래로그(TradeStore), KIS 잔고/휴장일 조회, /trade 라우트(가짜 브로커).

실주문/실계좌 없이 검증 가능한 부분만 다룬다. conftest의 _isolate_config가 config를 격리.
"""

from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from orchestrator.api import create_app
from orchestrator.lean import RunRequest, RunResult
from orchestrator.persistence import RunStore, TradeStore
from brokers.kis import KisClient


# ── TradeStore ──────────────────────────────────────────────────────────────
def test_trade_store_record_and_list(tmp_path):
    s = TradeStore(tmp_path / "t.db")
    s.record_trade({"trade_date": "2026-06-01", "ts": "2026-06-01T09:05:00",
                    "ticker": "005930", "name": "삼성전자", "side": "buy",
                    "qty": 10, "price": 71000, "reason": "EMA"})
    rows = s.list_trades("2026-06-01")
    assert len(rows) == 1
    assert rows[0]["side"] == "BUY" and rows[0]["amount"] == 710000  # price*qty 자동계산
    assert s.list_trades("2026-06-02") == []


def test_trade_store_dates_and_adjacent(tmp_path):
    s = TradeStore(tmp_path / "t.db")
    for d in ("2026-06-01", "2026-06-03", "2026-06-05"):
        s.record_trade({"trade_date": d, "ticker": "A", "side": "BUY", "qty": 1, "price": 1})
    assert s.trade_dates() == ["2026-06-01", "2026-06-03", "2026-06-05"]
    assert s.adjacent_date("2026-06-03", -1) == "2026-06-01"
    assert s.adjacent_date("2026-06-03", +1) == "2026-06-05"
    assert s.adjacent_date("2026-06-05", +1) is None
    assert s.adjacent_date("2026-06-04", -1) == "2026-06-03"  # 기록 없는 날 기준도 동작


def test_trade_store_daily_pnl(tmp_path):
    s = TradeStore(tmp_path / "t.db")
    s.record_trade({"trade_date": "2026-06-01", "ticker": "A", "side": "SELL", "qty": 1,
                    "price": 100, "realized_pnl": 5000})
    s.record_trade({"trade_date": "2026-06-01", "ticker": "B", "side": "SELL", "qty": 1,
                    "price": 100, "realized_pnl": -2000})
    s.record_trade({"trade_date": "2026-06-01", "ticker": "C", "side": "BUY", "qty": 1,
                    "price": 100})  # 매수는 realized 없음
    assert s.daily_pnl("2026-06-01") == 3000
    assert s.daily_pnl("2026-06-02") == 0


# ── KIS 잔고/휴장일 조회 ────────────────────────────────────────────────────
class FakeResp:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, get_responses=None):
        self.get_responses = list(get_responses or [])
        self.get_calls = []

    def post(self, url, data=None, headers=None, timeout=None):
        return FakeResp(200, {"access_token": "TOK", "expires_in": 86400})

    def get(self, url, headers=None, params=None, timeout=None):
        self.get_calls.append({"url": url, "headers": headers, "params": params})
        return self.get_responses.pop(0) if self.get_responses else FakeResp(200, {"rt_cd": "0"})


def _client(tmp_path, session, env="demo"):
    return KisClient("appkey12345678", "secret", env=env,
                     token_cache_path=tmp_path / ".kis_token.json", session=session)


def test_fetch_balance_normalizes(tmp_path):
    payload = {"rt_cd": "0",
               "output1": [
                   {"pdno": "005930", "prdt_name": "삼성전자", "hldg_qty": "10",
                    "pchs_avg_pric": "65000", "prpr": "71000", "evlu_amt": "710000",
                    "evlu_pfls_amt": "60000", "evlu_pfls_rt": "9.23"},
                   {"pdno": "000660", "hldg_qty": "0"},  # 0주 → 제외
               ],
               "output2": [{"dnca_tot_amt": "1000000", "prvs_rcdl_excc_amt": "950000",
                            "tot_evlu_amt": "710000", "nass_amt": "1660000"}]}
    sess = FakeSession(get_responses=[FakeResp(200, payload)])
    bal = _client(tmp_path, sess, env="demo").fetch_balance("12345678", "01")
    assert len(bal["holdings"]) == 1
    h = bal["holdings"][0]
    assert h["ticker"] == "005930" and h["qty"] == 10 and h["avg_price"] == 65000
    assert h["pnl"] == 60000 and h["pnl_pct"] == 9.23
    assert bal["deposit"] == 1000000 and bal["d2_deposit"] == 950000
    # 모의 env → VTTC8434R TR 사용
    assert sess.get_calls[0]["headers"]["tr_id"] == "VTTC8434R"


def test_fetch_balance_real_env_tr(tmp_path):
    sess = FakeSession(get_responses=[FakeResp(200, {"rt_cd": "0", "output1": [], "output2": []})])
    _client(tmp_path, sess, env="real").fetch_balance("12345678", "01")
    assert sess.get_calls[0]["headers"]["tr_id"] == "TTTC8434R"


def test_check_market_open(tmp_path):
    payload = {"rt_cd": "0", "output": [{"bass_dt": "20260601", "opnd_yn": "Y"}]}
    sess = FakeSession(get_responses=[FakeResp(200, payload)])
    assert _client(tmp_path, sess).check_market_open(date(2026, 6, 1)) is True
    closed = {"rt_cd": "0", "output": [{"bass_dt": "20260606", "opnd_yn": "N"}]}
    sess2 = FakeSession(get_responses=[FakeResp(200, closed)])
    assert _client(tmp_path, sess2).check_market_open(date(2026, 6, 6)) is False


# ── /trade 라우트 (가짜 브로커 주입) ────────────────────────────────────────
class FakeRunner:
    def run_backtest(self, request: RunRequest, on_start=None) -> RunResult:
        return RunResult("x", 0, {}, Path("/r"), Path("/r/run.log"), None)


class FakeBroker:
    name = "kis"
    label = "한국투자증권 (KIS)"
    def account_info(self):
        return {"broker": "kis", "broker_label": self.label, "account_no": "1234****-01",
                "account_type": "종합매매", "env": "demo"}
    def balance(self):
        return {"deposit": 1000000, "buying_power": 950000, "total_eval": 710000,
                "total_purchase": 650000, "total_pnl": 60000, "total_pnl_pct": 9.23,
                "net_asset": 1660000,
                "items": [{"ticker": "005930", "name": "삼성전자", "qty": 10, "avg_price": 65000,
                           "cur_price": 71000, "eval_amount": 710000, "pnl": 60000, "pnl_pct": 9.23}]}
    def market_status(self):
        return {"open": True, "session": "regular", "is_holiday": False,
                "env": "demo", "as_of": "2026-06-01 10:00"}


@pytest.fixture
def isolated_config(tmp_path, monkeypatch):
    from orchestrator import config
    monkeypatch.setattr(config, "CONFIG_LOCAL", tmp_path / "config.local.yaml")
    monkeypatch.setenv("LEAN_DATA_DIR", str(tmp_path / "data"))
    return config


@pytest.fixture
def client(tmp_path, isolated_config):
    app = create_app(runner=FakeRunner(), store=RunStore(tmp_path / "ui.db"),
                     trade_store=TradeStore(tmp_path / "trade.db"),
                     get_broker=lambda: (FakeBroker(), None))
    return TestClient(app)


def test_trade_page_renders(client):
    r = client.get("/trade")
    assert r.status_code == 200
    assert "자동매매" in r.text and "삼성전자" in r.text  # B 잔고 표
    assert "장중" in r.text                              # E 장상태 배지
    assert "모의투자(demo)" in r.text                    # A env 배지


def test_trade_page_graceful_when_broker_missing(tmp_path, isolated_config):
    app = create_app(runner=FakeRunner(), store=RunStore(tmp_path / "ui2.db"),
                     trade_store=TradeStore(tmp_path / "trade2.db"),
                     get_broker=lambda: (None, "KIS 키를 입력하세요"))
    c = TestClient(app)
    r = c.get("/trade")
    assert r.status_code == 200 and "KIS 키를 입력하세요" in r.text  # 페이지는 안 깨짐


def test_toggle_blocks_real_when_unarmed(client, isolated_config):
    isolated_config.save_live_config({"env": "real", "armed": False})
    r = client.post("/trade/toggle", data={"enabled": "1"}, follow_redirects=False)
    assert r.status_code == 303 and "error" in r.headers["location"]
    assert isolated_config.get_live_config()["enabled"] is False  # 켜지지 않음


def test_toggle_demo_allowed(client, isolated_config):
    isolated_config.set_broker("kis_demo")  # 모의투자 증권사 → 무장 없이 켜기 허용
    client.post("/trade/toggle", data={"enabled": "1"}, follow_redirects=False)
    assert isolated_config.get_live_config()["enabled"] is True


def test_settings_broker_preview(client, isolated_config):
    # 드롭다운 변경 = ?broker= 미리보기. 저장된 증권사(기본 kis)와 다르면 해당 슬롯 + '미저장' 표시.
    r = client.get("/settings?broker=kis_demo")
    assert "KIS 모의 App Key" in r.text and "미저장" in r.text
    # 미리보기 파라미터 없으면 저장된 증권사(kis) 그대로 → '미저장' 없음
    assert "미저장" not in client.get("/settings").text


def test_arm_saves_safety_settings(client, isolated_config):
    client.post("/trade/arm", data={"env": "real", "armed": "1",
                                    "max_order_amount": "300000", "hts_id": "myhts"},
                follow_redirects=False)
    lc = isolated_config.get_live_config()
    assert lc["env"] == "real" and lc["armed"] is True
    assert lc["max_order_amount"] == 300000 and lc["hts_id"] == "myhts"
