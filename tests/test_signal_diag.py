"""신호 진단(추정) 단위 테스트 — read 함수 주입으로 LEAN/실데이터 없이."""

from orchestrator import signal_diag as sd
from orchestrator.signal_diag import analyze_run, select_today


def test_analyze_run_none_when_incomplete():
    assert analyze_run({}, "/x") is None
    assert analyze_run({"signals": {}, "rule": "", "universe": [], "start": "", "end": ""}, "/x") is None


def test_analyze_run_flags_blocking_signal(monkeypatch):
    # 상승 추세(EMA UP) + 외인·기관 순매도(FLOW 0%) → 'EMA AND FLOW'는 FLOW가 매수를 막는다.
    prices = [{"date": f"2026-01-{i:02d}", "close": 100.0 + i} for i in range(1, 32)]
    flows = [{"date": p["date"], "foreign": -1000, "institution": -1000, "individual": 0} for p in prices]
    monkeypatch.setattr(sd, "read_price_daily", lambda d, t: prices)
    monkeypatch.setattr(sd, "read_flow", lambda d, t: flows)
    monkeypatch.setattr(sd, "_read_fund", lambda d, t: [])
    spec = {
        "signals": {
            "EMA": {"type": "ema", "params": {"fast": 5, "slow": 10}},
            "FLOW": {"type": "flow", "params": {"lookback": 5, "foreign": 1, "institution": 1, "individual": 0}},
        },
        "rule": "EMA AND FLOW", "universe": ["005930"],
        "start": "2026-01-01", "end": "2026-01-31",
    }
    r = analyze_run(spec, "/x")
    assert r["up_pct"]["FLOW"] == 0       # 순매도 → 수급 UP 0%
    assert r["up_pct"]["EMA"] > 0         # 상승추세 → EMA 일부 UP
    assert r["buy_pct"] == 0.0            # FLOW가 막아 매수신호 0
    assert "FLOW" in r["blockers"] and "EMA" not in r["blockers"]


def test_analyze_run_buys_when_all_up(monkeypatch):
    # 상승 추세 + 순매수 → 'EMA AND FLOW' 매수신호 발생.
    prices = [{"date": f"2026-01-{i:02d}", "close": 100.0 + i} for i in range(1, 32)]
    flows = [{"date": p["date"], "foreign": 1000, "institution": 1000, "individual": 0} for p in prices]
    monkeypatch.setattr(sd, "read_price_daily", lambda d, t: prices)
    monkeypatch.setattr(sd, "read_flow", lambda d, t: flows)
    monkeypatch.setattr(sd, "_read_fund", lambda d, t: [])
    spec = {
        "signals": {
            "EMA": {"type": "ema", "params": {"fast": 5, "slow": 10}},
            "FLOW": {"type": "flow", "params": {"lookback": 5, "foreign": 1, "institution": 1, "individual": 0}},
        },
        "rule": "EMA AND FLOW", "universe": ["005930"],
        "start": "2026-01-01", "end": "2026-01-31",
    }
    r = analyze_run(spec, "/x")
    assert r["buy_pct"] > 0 and r["blockers"] == []


# ── select_today: 라이브 담을/뺄 종목 미리보기 ───────────────────────────────
def _up_prices():
    # 상승 추세(EMA UP) — 매수신호.
    return [{"date": f"2026-01-{i:02d}", "close": 100.0 + i, "volume": 1000} for i in range(1, 32)]


def _down_prices():
    # 하락 추세(EMA DOWN) — 청산신호.
    return [{"date": f"2026-01-{i:02d}", "close": 200.0 - i, "volume": 1000} for i in range(1, 32)]


def test_select_today_none_when_incomplete():
    assert select_today({}, "/x", ["005930"]) is None
    assert select_today({"signals": {"E": {"type": "ema"}}, "rule": "E"}, "/x", []) is None  # 유니버스 없음


def test_select_today_buys_and_sells(monkeypatch):
    # 005930 상승→담을(미보유), 000660 하락+보유→뺄, 035720 하락 미보유→무시.
    series = {"005930": _up_prices(), "000660": _down_prices(), "035720": _down_prices()}
    monkeypatch.setattr(sd, "read_price_daily", lambda d, t: series[t])
    monkeypatch.setattr(sd, "read_flow", lambda d, t: [])
    monkeypatch.setattr(sd, "_read_fund", lambda d, t: [])
    spec = {"signals": {"EMA": {"type": "ema", "params": {"fast": 5, "slow": 10}}}, "rule": "EMA"}
    sel = select_today(spec, "/x", ["005930", "000660", "035720"], held=["000660"])
    assert [b["ticker"] for b in sel["buys"]] == ["005930"]
    assert sel["buys"][0]["held"] is False and sel["buys"][0]["reason"] == "EMA"
    assert [s["ticker"] for s in sel["sells"]] == ["000660"]  # 하락이어도 보유한 것만 청산
    assert sel["ref_date"] == "2026-01-31"


def test_select_today_held_up_marked_and_cap(monkeypatch):
    # 매수 후보 3개(모두 상승), max_positions=2 → 유동성 상위 2개만, 1개는 cut.
    def prices(vol):
        return [{"date": f"2026-01-{i:02d}", "close": 100.0 + i, "volume": vol} for i in range(1, 32)]
    series = {"A": prices(300), "B": prices(200), "C": prices(100)}
    monkeypatch.setattr(sd, "read_price_daily", lambda d, t: series[t])
    monkeypatch.setattr(sd, "read_flow", lambda d, t: [])
    monkeypatch.setattr(sd, "_read_fund", lambda d, t: [])
    spec = {"signals": {"EMA": {"type": "ema", "params": {"fast": 5, "slow": 10}}},
            "rule": "EMA", "max_positions": 2}
    sel = select_today(spec, "/x", ["A", "B", "C"], held=["A"])
    assert {b["ticker"] for b in sel["buys"]} == {"A", "B"}      # 유동성 상위 2(거래량 큰 순)
    assert [c["ticker"] for c in sel["cut"]] == ["C"]           # 한도 초과로 제외
    assert next(b for b in sel["buys"] if b["ticker"] == "A")["held"] is True


def test_select_today_missing_and_unmanaged(monkeypatch):
    monkeypatch.setattr(sd, "read_price_daily", lambda d, t: _up_prices() if t == "005930" else [])
    monkeypatch.setattr(sd, "read_flow", lambda d, t: [])
    monkeypatch.setattr(sd, "_read_fund", lambda d, t: [])
    spec = {"signals": {"EMA": {"type": "ema", "params": {"fast": 5, "slow": 10}}}, "rule": "EMA"}
    sel = select_today(spec, "/x", ["005930", "999999"], held=["111111"])
    assert sel["missing"] == ["999999"]            # 일봉 미적재 → 평가 제외
    assert sel["unmanaged"] == ["111111"]          # 보유하나 대상종목 밖
