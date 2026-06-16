"""토스(Toss) 클라이언트/브로커 단위 테스트 — 가짜 HTTP 세션 주입(실제 네트워크·키 미사용)."""

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from brokers.toss import TossClient, TossError
from brokers.toss_broker import TossBroker


class FakeResp:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.text = text

    def json(self):
        return self._payload


class FakeSession:
    """post=토큰발급(form), get=경로별 라우팅. 받은 form data/headers/params를 기록한다."""

    def __init__(self, routes=None, token_payload=None):
        self.token_payload = token_payload or {"access_token": "TOK", "expires_in": 86400}
        self.routes = routes or {}      # path-substring → payload(dict, BFF 봉투 그대로)
        self.post_count = 0
        self.post_data = None
        self.get_calls = []

    def post(self, url, data=None, headers=None, timeout=None):
        self.post_count += 1
        self.post_data = data
        return FakeResp(200, self.token_payload)

    def get(self, url, headers=None, params=None, timeout=None):
        self.get_calls.append({"url": url, "headers": headers, "params": params})
        # 가장 길게 매칭되는 경로의 payload 반환(없으면 빈 result).
        best = None
        for path, payload in self.routes.items():
            if path in url and (best is None or len(path) > len(best[0])):
                best = (path, payload)
        return FakeResp(200, best[1] if best else {"result": {}})


_ACCOUNTS = {"result": [{"accountNo": "12345678901", "accountSeq": 7, "accountType": "BROKERAGE"}]}
_CANDLE_ROUTE = "/api/v1/candles"


def _client(tmp_path, routes):
    return TossClient("client_id_xyz", "secret",
                      token_cache_path=tmp_path / ".toss_token.json",
                      session=FakeSession(routes))


def test_requires_keys(tmp_path):
    with pytest.raises(TossError):
        TossClient("", "", token_cache_path=tmp_path / "t.json")


def test_token_issue_form_and_disk_cache(tmp_path):
    sess = FakeSession()
    c1 = TossClient("client_id_xyz", "sek", token_cache_path=tmp_path / ".toss_token.json", session=sess)
    assert c1.access_token() == "TOK"
    assert c1.access_token() == "TOK"            # 메모리 캐시 — 재발급 없음
    assert sess.post_count == 1
    # OAuth2 client-credentials form 본문에 client_id/secret이 실린다.
    assert sess.post_data["grant_type"] == "client_credentials"
    assert sess.post_data["client_id"] == "client_id_xyz"
    # 새 클라이언트(같은 캐시/키) → 디스크 토큰 재사용, 재발급 안 함.
    sess2 = FakeSession()
    c2 = TossClient("client_id_xyz", "sek", token_cache_path=tmp_path / ".toss_token.json", session=sess2)
    assert c2.access_token() == "TOK" and sess2.post_count == 0


def test_account_seq_picks_brokerage_and_caches(tmp_path):
    routes = {"/api/v1/accounts": {"result": [
        {"accountNo": "999", "accountSeq": 3, "accountType": "PENSION_SAVINGS"},
        {"accountNo": "12345678901", "accountSeq": 7, "accountType": "BROKERAGE"},
    ]}}
    c = _client(tmp_path, routes)
    assert c.account_seq() == 7              # 종합매매(BROKERAGE) 우선
    assert c.account_no() == "12345678901"
    n_accounts = sum(1 for g in c._session.get_calls if "/accounts" in g["url"])
    c.account_seq()                          # 두 번째 호출은 캐시 — 추가 조회 없음
    n_accounts2 = sum(1 for g in c._session.get_calls if "/accounts" in g["url"])
    assert n_accounts == 1 and n_accounts2 == 1


def test_fetch_balance_normalizes_kr_only(tmp_path):
    routes = {
        "/api/v1/accounts": _ACCOUNTS,
        "/api/v1/holdings": {"result": {
            "marketValue": {"amount": {"krw": "7200000"}},
            "items": [
                {"symbol": "005930", "name": "삼성전자", "marketCountry": "KR", "currency": "KRW",
                 "quantity": "100", "lastPrice": "72000", "averagePurchasePrice": "65000",
                 "marketValue": {"amount": "7200000"},
                 "profitLoss": {"amount": "700000", "rate": "0.1077"}},
                {"symbol": "AAPL", "name": "Apple", "marketCountry": "US", "currency": "USD",
                 "quantity": "10", "lastPrice": "178.5", "averagePurchasePrice": "155",
                 "marketValue": {"amount": "1785"}, "profitLoss": {"amount": "232", "rate": "0.14"}},
            ],
        }},
        "/api/v1/buying-power": {"result": {"currency": "KRW", "cashBuyingPower": "5000000"}},
    }
    c = _client(tmp_path, routes)
    b = c.fetch_balance()
    assert len(b["holdings"]) == 1                     # 미국주식(AAPL) 제외, 국내만
    h = b["holdings"][0]
    assert h["ticker"] == "005930" and h["qty"] == 100
    assert h["avg_price"] == 65000 and h["cur_price"] == 72000
    assert h["eval_amount"] == 7200000 and h["pnl"] == 700000
    assert abs(h["pnl_pct"] - 10.77) < 1e-6           # 0.1077 → 10.77%
    assert b["buying_power"] == 5000000 and b["deposit"] == 5000000
    assert b["total_eval"] == 7200000


def test_check_market_open(tmp_path):
    day = date(2026, 6, 16)
    open_routes = {"/api/v1/market-calendar/KR": {"result": {
        "today": {"date": "2026-06-16", "integrated": {"regularMarket": {"startTime": "x"}}}}}}
    assert _client(tmp_path, open_routes).check_market_open(day) is True
    # 휴장/주말: today.date가 기준일과 다르면 거래일 아님.
    closed_routes = {"/api/v1/market-calendar/KR": {"result": {
        "today": {"date": "2026-06-15", "integrated": {"regularMarket": {"startTime": "x"}}}}}}
    assert _client(tmp_path, closed_routes).check_market_open(day) is False


def _candle(ts, o, h, low, c, v):
    return {"timestamp": ts, "openPrice": str(o), "highPrice": str(h),
            "lowPrice": str(low), "closePrice": str(c), "volume": str(v)}


def test_fetch_minute_normalizes_and_orders(tmp_path):
    # 한 페이지에 09:00,09:01만 와서 earliest<=open이라 1회로 종료. 오름차순·ms·time 정규화 확인.
    page = {"result": {"candles": [
        _candle("2026-06-01T09:01:00+09:00", 101, 102, 100, 101, 5),
        _candle("2026-06-01T09:00:00+09:00", 100, 101, 99, 100, 7),
    ], "nextBefore": "2026-06-01T09:00:00+09:00"}}
    c = _client(tmp_path, {_CANDLE_ROUTE: page})
    rows = c.fetch_minute("005930", date(2026, 6, 1))
    assert [r["ms"] for r in rows] == [9 * 3600 * 1000, (9 * 3600 + 60) * 1000]
    assert rows[0]["close"] == 100 and rows[0]["volume"] == 7 and isinstance(rows[0]["close"], int)
    assert rows[1]["time"] == "090100"
    # before 커서는 마감초+1초(마감봉 포함), interval=1m.
    call = c._session.get_calls[0]["params"]
    assert call["before"] == "2026-06-01T15:30:59+09:00" and call["interval"] == "1m"


def test_fetch_minute_filters_other_day_and_before_open(tmp_path):
    page = {"result": {"candles": [
        _candle("2026-06-01T09:00:00+09:00", 1, 1, 1, 1, 1),
        _candle("2026-06-01T08:59:00+09:00", 2, 2, 2, 2, 2),   # 장 시작 전 제외
        _candle("2026-05-29T15:30:00+09:00", 3, 3, 3, 3, 3),   # 다른 날 제외
    ], "nextBefore": None}}
    c = _client(tmp_path, {_CANDLE_ROUTE: page})
    rows = c.fetch_minute("005930", date(2026, 6, 1))
    assert [r["time"] for r in rows] == ["090000"]


def test_minute_etl_round_trip_via_toss(tmp_path):
    # TossClient.fetch_minute가 ingest_minute(KIS와 동일 인터페이스)로 그대로 적재되는지 확인.
    from etl.kis_minute import ingest_minute
    from etl.lean_format import read_equity_minute
    from market.krx import KRX_MARKET

    class FakeTossMinute:
        def fetch_minute(self, ticker, day, **kw):
            if day == date(2026, 6, 1):  # 월요일
                return [{"ms": 9 * 3600 * 1000, "time": "090000", "open": 100, "high": 110,
                         "low": 95, "close": 105, "volume": 50}]
            return []

    info = ingest_minute("005930", date(2026, 6, 1), date(2026, 6, 2),
                         data_dir=tmp_path, client=FakeTossMinute())
    assert info["days"] == 1 and info["bars"] == 1
    back = read_equity_minute(tmp_path, KRX_MARKET, "005930", date(2026, 6, 1))
    assert len(back) == 1 and back[0].close == 105.0 and back[0].ms == 9 * 3600 * 1000


def test_account_header_sent_on_scoped_calls(tmp_path):
    routes = {"/api/v1/accounts": _ACCOUNTS,
              "/api/v1/buying-power": {"result": {"cashBuyingPower": "100"}}}
    c = _client(tmp_path, routes)
    c.fetch_buying_power("KRW")
    bp_call = next(g for g in c._session.get_calls if "buying-power" in g["url"])
    assert bp_call["headers"]["X-Tossinvest-Account"] == "7"
    assert bp_call["headers"]["authorization"] == "Bearer TOK"


# ── TossBroker (읽기 어댑터) ────────────────────────────────────────────────
class FakeTossClient:
    def fetch_balance(self):
        return {"holdings": [{"ticker": "005930", "name": "삼성전자", "qty": 10,
                              "avg_price": 60000, "cur_price": 66000, "eval_amount": 660000,
                              "pnl": 60000, "pnl_pct": 10.0}],
                "deposit": 1000000, "d2_deposit": 0, "total_eval": 660000,
                "net_asset": 1660000, "buying_power": 1000000}

    def account_no(self):
        return "12345678901"

    def check_market_open(self, day):
        return True


def test_toss_broker_balance_and_account():
    b = TossBroker("cid", "sec", client=FakeTossClient())
    info = b.account_info()
    assert info["broker"] == "toss" and info["env"] == "real"
    assert info["account_no"].startswith("1234") and "*" in info["account_no"]
    bal = b.balance()
    assert bal["buying_power"] == 1000000 and bal["total_pnl"] == 60000
    assert bal["total_purchase"] == 600000 and abs(bal["total_pnl_pct"] - 10.0) < 1e-6
    assert bal["items"][0]["ticker"] == "005930"


def test_toss_broker_market_status_regular():
    now = datetime(2026, 6, 16, 10, 0, tzinfo=ZoneInfo("Asia/Seoul"))
    b = TossBroker("cid", "sec", client=FakeTossClient(), now_fn=lambda: now)
    ms = b.market_status()
    assert ms["open"] is True and ms["session"] == "regular" and ms["env"] == "real"


def test_toss_broker_has_no_trades_method():
    # Toss는 종료(CLOSED) 주문 조회 미지원 → trades 메서드를 두지 않아 BrokerCache가 자체 거래로그로 폴백.
    b = TossBroker("cid", "sec", client=FakeTossClient())
    assert not hasattr(b, "trades")


# ── config / 라이브 배선 (conftest의 _isolate_config가 config.local.yaml을 격리) ──────
def test_toss_credentials_and_broker_env():
    from orchestrator import config
    config.set_broker("toss")
    assert config.broker_env() == "real"                 # Toss는 실전 단일
    config.save_secrets({"toss_client_id": "cid", "toss_client_secret": "csec"})
    cred = config.get_toss_credentials()
    assert cred["client_id"] == "cid" and cred["client_secret"] == "csec"


def test_toss_live_start_ok_requires_creds():
    from orchestrator import config
    config.set_broker("toss")
    config.set_live_enabled(True)
    ok, why = config.live_start_ok()
    assert ok is False and "Client" in why               # 키 없으면 거부(HTS ID 요구 아님)
    config.save_secrets({"toss_client_id": "cid", "toss_client_secret": "csec"})
    ok, why = config.live_start_ok()
    assert ok is True                                     # Toss는 HTS ID 불필요


def test_get_trading_broker_dispatches_toss():
    from orchestrator import config
    from brokers.kis_broker import get_trading_broker
    config.set_broker("toss")
    broker, err = get_trading_broker()
    assert broker is None and "Client ID/Secret" in err   # 키 없으면 사유 반환
    config.save_secrets({"toss_client_id": "cid", "toss_client_secret": "csec"})
    broker, err = get_trading_broker()
    assert err is None and broker.name == "toss"
    assert isinstance(broker, TossBroker)


def test_build_toss_live_config_injects_keys():
    from orchestrator.lean.runner import RunRequest, build_toss_live_config
    from pathlib import Path
    req = RunRequest(strategy_path="strategies/RuleStrategy.py", data_folder=".",
                     algorithm_type="RuleStrategy")
    cfg = build_toss_live_config(req, Path("."), "live-x",
                                 live={"max_order_amount": 500000},
                                 toss={"client_id": "cid", "client_secret": "csec"},
                                 token_cache="/tmp/.toss_token.json")
    assert cfg["environment"] == "live-toss"
    assert cfg["toss-client-id"] == "cid" and cfg["toss-client-secret"] == "csec"
    assert cfg["toss-max-order-amount"] == "500000"
    assert cfg["environments"]["live-toss"]["live-mode-brokerage"] == "TossBrokerage"
    assert cfg["environments"]["live-toss"]["data-queue-handler"] == ["TossBrokerage"]
    # KIS 전용 키는 토스 config에 없어야 한다.
    assert "kis-app-key" not in cfg
