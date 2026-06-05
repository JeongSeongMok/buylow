"""신호 진단(추정) 단위 테스트 — read 함수 주입으로 LEAN/실데이터 없이."""

from orchestrator import signal_diag as sd
from orchestrator.signal_diag import analyze_run


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
