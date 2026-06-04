"""KIS 클라이언트/소스 단위 테스트 — 가짜 HTTP 세션 주입(실제 네트워크·키 미사용)."""

import json
import threading
import time

import pytest

from brokers.kis import KisClient, KisError, _TokenBucket
from etl.sources import KisSource, Bar
from datetime import date


class FakeResp:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    def json(self):
        return self._payload


class FakeSession:
    """post=토큰발급, get=시세조회. 호출 횟수와 받은 헤더/파라미터를 기록한다."""

    def __init__(self, token_payload=None, get_responses=None):
        self.token_payload = token_payload or {"access_token": "TOK", "expires_in": 86400}
        self.get_responses = list(get_responses or [])
        self.post_count = 0
        self.get_calls = []

    def post(self, url, data=None, headers=None, timeout=None):
        self.post_count += 1
        return FakeResp(200, self.token_payload)

    def get(self, url, headers=None, params=None, timeout=None):
        self.get_calls.append({"url": url, "headers": headers, "params": params})
        resp = self.get_responses.pop(0) if self.get_responses else FakeResp(200, {"rt_cd": "0", "output2": []})
        return resp


def _client(tmp_path, session):
    return KisClient("appkey12345678", "secret", env="real",
                     token_cache_path=tmp_path / ".kis_token.json", session=session)


def _row(d, o, h, low, c, v):
    return {"stck_bsop_date": d, "stck_oprc": str(o), "stck_hgpr": str(h),
            "stck_lwpr": str(low), "stck_clpr": str(c), "acml_vol": str(v)}


def test_requires_keys(tmp_path):
    with pytest.raises(KisError):
        KisClient("", "", token_cache_path=tmp_path / "t.json")


def test_token_issue_and_disk_cache_reuse(tmp_path):
    sess = FakeSession()
    c1 = _client(tmp_path, sess)
    assert c1.access_token() == "TOK"
    assert c1.access_token() == "TOK"   # 메모리 캐시 — 재발급 없음
    assert sess.post_count == 1
    # 새 클라이언트(같은 캐시 파일/키) → 디스크 토큰 재사용, 재발급 안 함
    sess2 = FakeSession()
    c2 = _client(tmp_path, sess2)
    assert c2.access_token() == "TOK"
    assert sess2.post_count == 0
    assert (tmp_path / ".kis_token.json").exists()


def test_token_headers_carry_appkey(tmp_path):
    sess = FakeSession(get_responses=[FakeResp(200, {"rt_cd": "0", "output2": []})])
    c = _client(tmp_path, sess)
    c.fetch_today("005930", date(2026, 6, 1))
    h = sess.get_calls[0]["headers"]
    assert h["authorization"] == "Bearer TOK"
    assert h["appkey"] == "appkey12345678" and h["tr_id"] == "FHKST03010100"


def test_fetch_daily_normalizes_and_sorts(tmp_path):
    # 내림차순으로 와도 오름차순 정규화, 가격은 정수, 범위 밖/빈 행 제외
    out2 = [
        _row("20260603", 100, 110, 90, 105, 10),
        _row("20260602", 100, 120, 95, 118, 20),
        _row("20260601", 100, 105, 99, 101, 30),
        {"stck_bsop_date": "", "stck_clpr": ""},  # 빈 행
    ]
    sess = FakeSession(get_responses=[FakeResp(200, {"rt_cd": "0", "output2": out2})])
    c = _client(tmp_path, sess)
    rows = c.fetch_daily("005930", date(2026, 6, 1), date(2026, 6, 3))
    assert [r["day"] for r in rows] == [date(2026, 6, 1), date(2026, 6, 2), date(2026, 6, 3)]
    assert rows[1]["close"] == 118 and isinstance(rows[1]["close"], int)
    assert rows[2]["volume"] == 10


def test_fetch_daily_filters_outside_range(tmp_path):
    out2 = [_row("20260601", 1, 1, 1, 1, 1), _row("20260701", 2, 2, 2, 2, 2)]
    sess = FakeSession(get_responses=[FakeResp(200, {"rt_cd": "0", "output2": out2})])
    c = _client(tmp_path, sess)
    rows = c.fetch_daily("005930", date(2026, 6, 1), date(2026, 6, 30))
    assert [r["day"] for r in rows] == [date(2026, 6, 1)]  # 7/1 제외


def test_rt_cd_error_raises(tmp_path):
    sess = FakeSession(get_responses=[FakeResp(200, {"rt_cd": "1", "msg1": "조회 오류"})])
    c = _client(tmp_path, sess)
    with pytest.raises(KisError):
        c.fetch_today("005930", date(2026, 6, 1))


def test_http_error_raises(tmp_path):
    sess = FakeSession(get_responses=[FakeResp(500, {}, text="boom")])
    c = _client(tmp_path, sess)
    with pytest.raises(KisError):
        c.fetch_today("005930", date(2026, 6, 1))


def test_rate_limit_retries_then_succeeds(tmp_path):
    # KIS 레이트리밋(HTTP 500 + EGW00201) → 백오프 후 재시도 → 성공
    rl = FakeResp(500, {"rt_cd": "1", "msg_cd": "EGW00201", "msg1": "초당 거래건수 초과"})
    ok = FakeResp(200, {"rt_cd": "0", "output2": [_row("20260601", 1, 1, 1, 1, 1)]})
    sess = FakeSession(get_responses=[rl, rl, ok])
    c = KisClient("appkey12345678", "secret", env="real",
                  token_cache_path=tmp_path / ".kis_token.json", session=sess,
                  min_interval=0, backoff=0)  # 테스트는 지연 0
    rows = c.fetch_daily("005930", date(2026, 6, 1), date(2026, 6, 1))
    assert len(rows) == 1 and len(sess.get_calls) == 3  # 2번 재시도 후 성공


def test_rate_limit_exhausts_retries(tmp_path):
    rl = FakeResp(500, {"rt_cd": "1", "msg_cd": "EGW00201", "msg1": "초당 거래건수 초과"})
    sess = FakeSession(get_responses=[rl, rl, rl, rl, rl, rl])
    c = KisClient("appkey12345678", "secret", env="real",
                  token_cache_path=tmp_path / ".kis_token.json", session=sess,
                  min_interval=0, backoff=0, max_retries=2)
    with pytest.raises(KisError):
        c.fetch_today("005930", date(2026, 6, 1))
    assert len(sess.get_calls) == 3  # 최초 + 재시도 2회


def _mrow(hhmmss, o, h, low, c, v):
    return {"stck_cntg_hour": hhmmss, "stck_oprc": str(o), "stck_hgpr": str(h),
            "stck_lwpr": str(low), "stck_prpr": str(c), "cntg_vol": str(v)}


def test_fetch_minute_normalizes_and_orders(tmp_path):
    # 한 번 호출에 09:00,09:01만 와서 멈춤(둘 다 09:00 이후, earliest=open이라 종료)
    out2 = [_mrow("090100", 101, 102, 100, 101, 5), _mrow("090000", 100, 101, 99, 100, 7)]
    sess = FakeSession(get_responses=[FakeResp(200, {"rt_cd": "0", "output2": out2})])
    c = _client(tmp_path, sess)
    rows = c.fetch_minute("005930", date(2026, 6, 1))
    assert [r["ms"] for r in rows] == [9 * 3600 * 1000, (9 * 3600 + 60) * 1000]  # 오름차순
    assert rows[0]["close"] == 100 and rows[0]["volume"] == 7
    assert rows[1]["time"] == "090100"


def test_fetch_minute_filters_before_open(tmp_path):
    out2 = [_mrow("090000", 1, 1, 1, 1, 1), _mrow("085900", 2, 2, 2, 2, 2)]  # 08:59 장전 제외
    sess = FakeSession(get_responses=[FakeResp(200, {"rt_cd": "0", "output2": out2})])
    c = _client(tmp_path, sess)
    rows = c.fetch_minute("005930", date(2026, 6, 1))
    assert [r["time"] for r in rows] == ["090000"]


def test_minute_etl_round_trip(tmp_path):
    from etl.kis_minute import ingest_minute
    from etl.lean_format import read_equity_minute
    from market.krx import KRX_MARKET

    class FakeClient:
        def fetch_minute(self, ticker, day, **kw):
            if day == date(2026, 6, 1):  # 월요일
                return [{"ms": 9 * 3600 * 1000, "time": "090000", "open": 100, "high": 110,
                         "low": 95, "close": 105, "volume": 50}]
            return []

    info = ingest_minute("005930", date(2026, 6, 1), date(2026, 6, 2),
                         data_dir=tmp_path, client=FakeClient())
    assert info["days"] == 1 and info["bars"] == 1 and info["first"] == "2026-06-01"
    back = read_equity_minute(tmp_path, KRX_MARKET, "005930", date(2026, 6, 1))
    assert len(back) == 1
    assert back[0].close == 105.0 and back[0].ms == 9 * 3600 * 1000 and back[0].volume == 50


def test_minute_etl_skips_existing(tmp_path):
    from etl.kis_minute import ingest_minute
    from market.krx import KRX_MARKET
    from etl.lean_format import list_minute_days

    class CountingClient:
        def __init__(self): self.calls = 0
        def fetch_minute(self, ticker, day, **kw):
            self.calls += 1
            return [{"ms": 9 * 3600 * 1000, "time": "090000", "open": 100, "high": 100,
                     "low": 100, "close": 100, "volume": 1}]

    c = CountingClient()
    # 6/1(월) 적재
    ingest_minute("005930", date(2026, 6, 1), date(2026, 6, 1), data_dir=tmp_path,
                  client=c, today=date(2026, 6, 2))
    assert c.calls == 1
    # 다시 같은 날 → 디스크에 있으니 호출 0 (skip_existing 기본)
    info = ingest_minute("005930", date(2026, 6, 1), date(2026, 6, 1), data_dir=tmp_path,
                         client=c, today=date(2026, 6, 2))
    assert c.calls == 1 and info["skipped"] == 1
    assert list_minute_days(tmp_path, KRX_MARKET, "005930") == {date(2026, 6, 1)}


def test_minute_etl_reports_progress(tmp_path):
    from etl.kis_minute import ingest_minute

    class C:
        def fetch_minute(self, ticker, day, **kw):
            return [{"ms": 0, "time": "090000", "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}]

    msgs = []
    # 약 한 달(>20거래일) → 진행 콜백이 최소 1회 이상 호출됨
    ingest_minute("005930", date(2026, 1, 1), date(2026, 2, 28), data_dir=tmp_path,
                  client=C(), today=date(2026, 3, 1), on_progress=msgs.append)
    assert msgs and "거래일 처리" in msgs[-1]


def test_minute_etl_clamps_to_one_year(tmp_path):
    from etl.kis_minute import ingest_minute

    class C:
        def __init__(self): self.days = []
        def fetch_minute(self, ticker, day, **kw):
            self.days.append(day); return []

    c = C()
    # 3년 전~오늘 요청 → 약 1년으로 클램프되어 그 이전 날짜는 호출 안 함
    info = ingest_minute("005930", date(2023, 1, 1), date(2026, 6, 2), data_dir=tmp_path,
                         client=c, today=date(2026, 6, 2))
    assert info["clamped"] is True
    assert c.days and min(c.days) >= date(2025, 6, 2)  # today-365 이후만


class _FakeClock:
    """주입형 가상 시계 — sleep이 시간을 전진시켜 토큰버킷을 결정론적으로 검증한다."""
    def __init__(self):
        self.t = 0.0
    def now(self):
        return self.t
    def sleep(self, s):
        self.t += s


def test_token_bucket_enforces_rate_deterministically():
    clk = _FakeClock()
    tb = _TokenBucket(rate=10.0, burst=2.0, clock=clk.now, sleep=clk.sleep)
    # 버스트 2개는 즉시 통과(시간 0 그대로)
    tb.acquire(); tb.acquire()
    assert clk.t == 0.0
    # 이후엔 초당 10건 = 0.1초 간격으로만 통과
    tb.acquire()
    assert abs(clk.t - 0.1) < 1e-9
    tb.acquire()
    assert abs(clk.t - 0.2) < 1e-9


def test_token_bucket_zero_rate_is_unlimited():
    clk = _FakeClock()
    tb = _TokenBucket(rate=0.0, clock=clk.now, sleep=clk.sleep)
    for _ in range(100):
        tb.acquire()
    assert clk.t == 0.0  # 대기 없음


def test_token_bucket_caps_aggregate_across_threads():
    # 여러 스레드가 공유해도 '합산' 호출 수가 용량+경과×rate를 넘지 않아야 한다(실시간, 느슨한 상한).
    tb = _TokenBucket(rate=200.0, burst=5.0)
    counter = {"n": 0}
    clock_lock = threading.Lock()
    start = time.monotonic()

    def worker():
        for _ in range(20):
            tb.acquire()
            with clock_lock:
                counter["n"] += 1

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads: t.start()
    for t in threads: t.join()
    elapsed = time.monotonic() - start
    # 이론 상한: 버스트 + 경과×rate. 타이밍 여유(2배)를 둬 CI 흔들림에도 견고하게.
    assert counter["n"] == 160
    assert counter["n"] <= 5 + elapsed * 200.0 * 2 + 5


def test_minute_etl_parallel_uses_multiple_threads(tmp_path):
    # max_workers>1이면 거래일들을 여러 스레드에서 동시에 받는다(병렬 증명).
    from etl.kis_minute import ingest_minute

    class ThreadRecordingClient:
        def __init__(self):
            self.threads = set()
            self.lock = threading.Lock()
        def fetch_minute(self, ticker, day, **kw):
            with self.lock:
                self.threads.add(threading.get_ident())
            time.sleep(0.02)  # 다른 스레드와 겹치도록 잠깐 잡아둔다
            return [{"ms": 9 * 3600 * 1000, "time": "090000", "open": 1, "high": 1,
                     "low": 1, "close": 1, "volume": 1}]

    c = ThreadRecordingClient()
    info = ingest_minute("005930", date(2026, 1, 1), date(2026, 1, 31), data_dir=tmp_path,
                         client=c, today=date(2026, 2, 1), max_workers=4)
    assert info["days"] > 1
    assert len(c.threads) > 1  # 실제로 여러 스레드가 fetch를 수행


def test_minute_etl_parallel_first_last_sorted(tmp_path):
    # 완료 순서가 섞여도 first/last가 날짜순으로 올바른지(병렬 정렬 보장).
    from etl.kis_minute import ingest_minute

    class C:
        def fetch_minute(self, ticker, day, **kw):
            return [{"ms": 0, "time": "090000", "open": 1, "high": 1, "low": 1,
                     "close": 1, "volume": 1}]

    info = ingest_minute("005930", date(2026, 1, 1), date(2026, 1, 31), data_dir=tmp_path,
                         client=C(), today=date(2026, 2, 1), max_workers=4)
    assert info["first"] == "2026-01-01"   # 1/1은 목요일(거래일)
    assert info["last"] == "2026-01-30"    # 1/30은 금요일
    assert info["first"] < info["last"]


def test_list_minute_days_empty(tmp_path):
    from etl.lean_format import list_minute_days
    from market.krx import KRX_MARKET
    assert list_minute_days(tmp_path, KRX_MARKET, "000660") == set()


def test_kis_source_maps_to_bars(tmp_path):
    out2 = [_row("20260602", 100, 120, 95, 118, 20), _row("20260601", 100, 105, 99, 0, 30)]
    sess = FakeSession(get_responses=[FakeResp(200, {"rt_cd": "0", "output2": out2})])
    src = KisSource(client=_client(tmp_path, sess))
    bars = src.fetch_daily("005930", date(2026, 6, 1), date(2026, 6, 2))
    # 종가 0(거래정지)인 6/1 제외, 6/2만 Bar로
    assert len(bars) == 1 and isinstance(bars[0], Bar)
    assert bars[0].close == 118.0 and bars[0].day == date(2026, 6, 2)
