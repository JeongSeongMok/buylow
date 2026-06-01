"""유니버스 대량 ETL 테스트 — pykrx 결합이라 통합 테스트(로그인 필요, 기본 제외)."""

from datetime import date

import pytest


def test_ingest_all_market_skips_flow_when_no_trading_days(tmp_path, monkeypatch):
    # 신규 거래일이 0이면(주말/휴장 증분 등) 수급을 호출하지 않아야 함(에러 폭주 방지)
    import etl.universe as u
    import etl.flow as flow
    import orchestrator.config as cfg
    monkeypatch.setattr(u, "ingest_universe",
                        lambda *a, **k: {"ingested": 0, "trading_days": 0, "universe": 0, "market": "ALL"})
    monkeypatch.setattr(cfg, "apply_krx_credentials", lambda: True)
    calls = {"flow": 0}
    monkeypatch.setattr(flow, "ingest_flow", lambda *a, **k: calls.__setitem__("flow", calls["flow"] + 1))

    info = u.ingest_all_market(tmp_path, start=date(2026, 5, 30), end=date(2026, 6, 1))
    assert info["trading_days"] == 0
    assert info["flow_enabled"] is False
    assert calls["flow"] == 0  # 수급 호출 안 함


@pytest.mark.integration
def test_ingest_kospi200_small(tmp_path):
    from orchestrator.config import apply_krx_credentials
    if not apply_krx_credentials():
        pytest.skip("KRX 크리덴셜 미설정")
    from etl.universe import ingest_universe

    info = ingest_universe(date(2024, 1, 2), date(2024, 1, 5), "KOSPI200", tmp_path)
    assert info["universe"] >= 100
    assert info["ingested"] >= 100  # 대부분 적재
    # 삼성전자는 KOSPI200 구성종목 → 파일 존재
    assert (tmp_path / "equity" / "krx" / "daily" / "005930.zip").exists()
