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
    def trades(self, date_iso):
        return [{"ts": f"{date_iso}T10:00:00", "ticker": "005930", "name": "삼성전자",
                 "side": "BUY", "qty": 10, "price": 71000, "amount": 710000,
                 "realized_pnl": None, "reason": "KIS 체결"}]


@pytest.fixture
def isolated_config(tmp_path, monkeypatch):
    from orchestrator import config
    monkeypatch.setattr(config, "CONFIG_LOCAL", tmp_path / "config.local.yaml")
    monkeypatch.setenv("LEAN_DATA_DIR", str(tmp_path / "data"))
    return config


class FakeLiveManager:
    def __init__(self, running=False):
        self._running = running
        self.started = self.stopped = False
    def is_running(self):
        return self._running
    def start(self, runner, req):
        self.started = True; self._running = True; return "live-job"
    def stop(self):
        self.stopped = True; self._running = False; return True


@pytest.fixture
def live_manager():
    return FakeLiveManager()


@pytest.fixture
def client(tmp_path, isolated_config, live_manager):
    app = create_app(runner=FakeRunner(), store=RunStore(tmp_path / "ui.db"),
                     trade_store=TradeStore(tmp_path / "trade.db"),
                     get_broker=lambda: (FakeBroker(), None), live_manager=live_manager)
    c = TestClient(app)
    c.live_manager = live_manager  # 테스트에서 start/stop 호출 확인
    return c


def test_trade_page_renders(client):
    r = client.get("/trade")
    assert r.status_code == 200
    assert "자동매매" in r.text                          # D 토글
    assert "장중" in r.text                              # E 장상태(서버 렌더)
    assert "모의투자" in r.text                          # A 환경 배지(모의일 때만)
    # 잔고·매매내역은 진입 시 비동기 로드 — 자리표시 + hx 트리거
    assert "불러오는 중" in r.text
    assert 'hx-get="/trade/balance"' in r.text and "load, every 10s" in r.text


def test_trade_page_balance_is_lazy(client):
    # 진입 시 잔고 데이터는 서버 렌더에 없고(예수금/보유표), /trade/balance 비동기로만 온다.
    assert "예수금" not in client.get("/trade").text
    assert "예수금" in client.get("/trade/balance").text


def test_trade_page_hides_badge_for_real(tmp_path, isolated_config):
    # 실전(env=real)이면 '모의투자' 배지를 표시하지 않는다.
    class RealBroker(FakeBroker):
        def account_info(self):
            d = super().account_info(); d["env"] = "real"; return d
        def market_status(self):
            m = super().market_status(); m["env"] = "real"; return m
    app = create_app(runner=FakeRunner(), store=RunStore(tmp_path / "u3.db"),
                     trade_store=TradeStore(tmp_path / "t3.db"),
                     get_broker=lambda: (RealBroker(), None))
    r = TestClient(app).get("/trade")
    assert r.status_code == 200 and "모의투자" not in r.text


def test_trade_balance_partial_polls(client):
    # 잔고 부분 템플릿: 데이터 + 10초 폴링 속성.
    r = client.get("/trade/balance")
    assert r.status_code == 200
    assert "삼성전자" in r.text
    assert 'hx-get="/trade/balance"' in r.text and "every 10s" in r.text


def test_trade_trades_partial_polls(client):
    r = client.get("/trade/trades?date=2026-06-05")
    assert r.status_code == 200
    assert 'hx-get="/trade/trades?date=2026-06-05"' in r.text and 'every 10s' in r.text
    assert "삼성전자" in r.text  # 브로커 체결조회(trades) 결과 표시


# ── 오늘의 선정(담을/뺄 종목) ────────────────────────────────────────────────
def test_trade_page_includes_selection_lazyload(client):
    # 매매 탭에 '오늘의 선정' 카드 + 비동기 로드 트리거가 있고, 진입 시엔 자리표시(계산 중)만.
    r = client.get("/trade")
    assert r.status_code == 200
    assert "오늘의 선정" in r.text
    assert 'hx-get="/trade/selection"' in r.text and "load, every 30s" in r.text
    assert "선정 계산 중" in r.text  # 초기 렌더는 placeholder


def test_trade_selection_no_strategy(client):
    # 전략 미저장 → 안내문(섹션은 계산 안 함).
    r = client.get("/trade/selection")
    assert r.status_code == 200 and "전략이 없습니다" in r.text


def test_trade_selection_renders_buys_and_sells(client, monkeypatch):
    from orchestrator import config
    from orchestrator import signal_diag
    config.save_strategy({"signals": {"EMA": {"type": "ema"}}, "rule": "EMA"})
    config.save_live_universe(["005930", "000660"])
    # select_today를 카드 렌더만 검증하도록 고정(데이터 적재 불필요).
    monkeypatch.setattr(signal_diag, "select_today", lambda *a, **k: {
        "ref_date": "2026-06-05",
        "buys": [{"ticker": "000660", "held": False, "reason": "EMA", "date": "2026-06-05"}],
        "sells": [{"ticker": "005930", "reason": "EMA", "date": "2026-06-05"}],
        "cut": [], "max_positions": 0, "evaluated": 2, "missing": [], "stale": [], "unmanaged": [],
    })
    r = client.get("/trade/selection")
    assert r.status_code == 200
    assert "담을 종목" in r.text and "뺄 종목" in r.text
    assert "기준일 2026-06-05" in r.text
    assert "000660" in r.text and "005930" in r.text  # 매수·청산 후보 종목코드


def test_fetch_executions_normalizes(tmp_path):
    payload = {"rt_cd": "0", "output1": [
        {"ord_dt": "20260605", "ord_tmd": "100530", "pdno": "005930", "prdt_name": "삼성전자",
         "sll_buy_dvsn_cd": "02", "tot_ccld_qty": "10", "avg_prvs": "71000",
         "tot_ccld_amt": "710000", "ord_dvsn_name": "시장가"},
        {"pdno": "000660", "tot_ccld_qty": "0"},  # 미체결 → 제외
    ], "output2": {}}
    sess = FakeSession(get_responses=[FakeResp(200, payload)])
    rows = _client(tmp_path, sess, env="demo").fetch_executions("12345678", "01",
                                                                date(2026, 6, 5), date(2026, 6, 5))
    assert len(rows) == 1
    r0 = rows[0]
    assert r0["buy"] and r0["ticker"] == "005930" and r0["qty"] == 10 and r0["price"] == 71000
    assert sess.get_calls[0]["headers"]["tr_id"] == "VTTC0081R"  # 모의 TR


def test_kis_broker_trades_maps_executions():
    class FakeClient:
        def fetch_executions(self, cano, prdt, start, end):
            return [{"date": "20260605", "time": "100530", "ticker": "005930", "name": "삼성전자",
                     "buy": True, "qty": 10, "price": 71000, "amount": 710000, "reason": "시장가"}]
    from brokers.kis_broker import KisBroker
    b = KisBroker("k", "s", "12345678-01", env="demo", client=FakeClient())
    t = b.trades("2026-06-05")
    assert len(t) == 1
    assert t[0]["ts"] == "2026-06-05T10:05:30" and t[0]["side"] == "BUY"
    assert t[0]["realized_pnl"] is None


def test_trade_page_graceful_when_broker_missing(tmp_path, isolated_config):
    app = create_app(runner=FakeRunner(), store=RunStore(tmp_path / "ui2.db"),
                     trade_store=TradeStore(tmp_path / "trade2.db"),
                     get_broker=lambda: (None, "KIS 키를 입력하세요"))
    c = TestClient(app)
    r = c.get("/trade")
    assert r.status_code == 200 and "KIS 키를 입력하세요" in r.text  # 페이지는 안 깨짐


def _save_demo_strategy(cfg):
    cfg.save_strategy({"signals": {}, "rule": "EMA", "groups": [["EMA"]],
                       "period_days": 3, "resolution": "daily", "execution": {}})


def test_toggle_real_not_blocked_by_arming(client, isolated_config):
    # 무장 제거 — 실전(kis)도 전략·유니버스 준비되면 모의처럼 시작(어댑터 DLL 있을 때).
    isolated_config.set_broker("kis")
    isolated_config.save_secrets({"kis_hts_id": "H"})  # HTS ID 필수
    _save_demo_strategy(isolated_config)
    isolated_config.save_live_universe(["005930"])
    client.post("/trade/toggle", data={"enabled": "1"}, follow_redirects=False)
    from orchestrator.lean.environment import LAUNCHER_OUT
    if (LAUNCHER_OUT / "MyTrading.Kis.dll").exists():
        assert client.live_manager.started and isolated_config.get_live_config()["enabled"]
    else:
        assert client.live_manager.started is False  # 어댑터 없으면 차단(무장과 무관)


def test_toggle_on_requires_strategy(client, isolated_config):
    isolated_config.set_broker("kis_demo")
    r = client.post("/trade/toggle", data={"enabled": "1"}, follow_redirects=False)
    assert "error" in r.headers["location"]  # 전략 미저장 → 거부
    assert client.live_manager.started is False


def test_toggle_on_requires_universe(client, isolated_config):
    isolated_config.set_broker("kis_demo")
    _save_demo_strategy(isolated_config)
    r = client.post("/trade/toggle", data={"enabled": "1"}, follow_redirects=False)
    assert "error" in r.headers["location"]  # 유니버스 미선택 → 거부
    assert client.live_manager.started is False


def test_toggle_on_requires_hts_id(client, isolated_config):
    # HTS ID 없으면 전략·유니버스 준비돼도 거부(체결 자동확인 필수).
    isolated_config.set_broker("kis_demo")
    _save_demo_strategy(isolated_config)
    isolated_config.save_live_universe(["005930"])
    r = client.post("/trade/toggle", data={"enabled": "1"}, follow_redirects=False)
    assert "error" in r.headers["location"]
    assert client.live_manager.started is False


def test_toggle_on_starts_when_ready(client, isolated_config):
    # 모의 + 전략 + 유니버스 + HTS ID 준비 → 어댑터 DLL이 있으면 start, 없으면 어댑터 안내(가드 정상).
    isolated_config.set_broker("kis_demo")
    isolated_config.save_secrets({"kis_demo_hts_id": "H"})  # HTS ID 필수
    _save_demo_strategy(isolated_config)
    isolated_config.save_live_universe(["005930"])
    client.post("/trade/toggle", data={"enabled": "1"}, follow_redirects=False)
    from orchestrator.lean.environment import LAUNCHER_OUT
    if (LAUNCHER_OUT / "MyTrading.Kis.dll").exists():
        assert client.live_manager.started and isolated_config.get_live_config()["enabled"]
    else:
        assert client.live_manager.started is False  # 어댑터 없으면 차단


def test_toggle_off_stops(isolated_config, tmp_path):
    # 끄기 → 라이브 프로세스 stop 호출 + enabled False.
    lm = FakeLiveManager(running=True)
    app = create_app(runner=FakeRunner(), store=RunStore(tmp_path / "u.db"),
                     trade_store=TradeStore(tmp_path / "t.db"),
                     get_broker=lambda: (FakeBroker(), None), live_manager=lm)
    isolated_config.set_live_enabled(True)
    TestClient(app).post("/trade/toggle", data={"enabled": "0"}, follow_redirects=False)
    assert lm.stopped is True
    assert isolated_config.get_live_config()["enabled"] is False


def test_trade_universe_save(client, isolated_config):
    client.post("/trade/universe", data={"universe": "005930,000660"}, follow_redirects=False)
    assert isolated_config.get_live_universe() == ["005930", "000660"]


def test_settings_shows_active_broker_slots(client, isolated_config):
    # 설정 탭은 '활성 증권사'의 키 슬롯만 보여준다(미리보기 없음).
    isolated_config.set_broker("kis_demo")
    assert "KIS 모의 App Key" in client.get("/settings").text
    isolated_config.set_broker("kis")
    h = client.get("/settings").text
    assert "KIS App Key" in h and "KIS 모의 App Key" not in h


def test_set_broker_switches_active(client, isolated_config):
    # 드롭다운 선택 = 활성 증권사 즉시 전환.
    client.post("/settings/broker", data={"broker": "kis_demo"}, follow_redirects=False)
    assert isolated_config.get_broker() == "kis_demo"
    client.post("/settings/broker", data={"broker": "kis"}, follow_redirects=False)
    assert isolated_config.get_broker() == "kis"


def test_kis_conn_test_follows_active_broker(client, isolated_config):
    # 연동 테스트는 활성 증권사 기준으로 동작한다(키 미설정이라 '먼저 저장' 메시지, 라벨로 증권사 확인).
    isolated_config.set_broker("kis")
    assert "실전" in client.post("/settings/test/kis").json()["message"]
    isolated_config.set_broker("kis_demo")
    assert "모의투자" in client.post("/settings/test/kis").json()["message"]


def test_settings_clear_removes_active_broker_keys(client, isolated_config):
    isolated_config.set_broker("kis")
    isolated_config.save_secrets({"kis_app_key": "X", "kis_app_secret": "Y", "kis_account_no": "Z"})
    client.post("/settings/clear", follow_redirects=False)
    assert isolated_config.get_kis_credentials("kis")["app_key"] is None


def test_arm_saves_safety_settings(client, isolated_config):
    # 무장 제거 — /trade/arm은 선택적 주문금액 한도만 저장(HTS ID는 설정 탭 시크릿).
    client.post("/trade/arm", data={"max_order_amount": "300000"}, follow_redirects=False)
    lc = isolated_config.get_live_config()
    assert "armed" not in lc
    assert lc["max_order_amount"] == 300000


def test_settings_shows_hts_id_slot(client, isolated_config):
    # 설정 탭에 HTS ID가 증권사 시크릿으로 노출되고, 저장하면 라이브 설정이 도출된다.
    isolated_config.set_broker("kis")
    assert any(s["key"] == "kis_hts_id" for s in isolated_config.broker_secret_status("kis"))
    isolated_config.save_secrets({"kis_hts_id": "MYHTS"})
    assert isolated_config.get_live_config()["hts_id"] == "MYHTS"
