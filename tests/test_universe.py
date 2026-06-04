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


def test_update_all_market_backfills_missing_fundamental(tmp_path, monkeypatch):
    # 펀더멘털만 비어 있으면 → 펀더멘털(단면)만 과거 5년 백필. 수급 갭에 거래일 없으면 수급은 생략.
    import etl.universe as u
    import etl.catalog as cat
    import etl.fundamental as fund
    import orchestrator.config as cfg
    monkeypatch.setattr(cat, "latest_loaded_date",
                        lambda d, kind="price": {"price": "2026-05-29", "flow": "2026-05-29",
                                                 "fundamental": None}[kind])
    monkeypatch.setattr(u, "ingest_universe", lambda *a, **k: {"ingested": 0, "trading_days": 0})
    monkeypatch.setattr(cfg, "apply_krx_credentials", lambda: True)
    monkeypatch.setattr(u, "_trading_days", lambda s, e: [])  # 수급 갭에 거래일 없음 → 수급 생략
    cap = {}
    monkeypatch.setattr(fund, "ingest_fundamental_universe",
                        lambda start, end, data_dir, **k: cap.update(fund_start=start) or {"tickers": 7})
    flow_calls = {"n": 0}
    monkeypatch.setattr(u, "_ingest_flow_per_ticker",
                        lambda *a, **k: flow_calls.__setitem__("n", flow_calls["n"] + 1) or (0, 0))

    info = u.update_all_market(tmp_path)
    assert cap["fund_start"].year <= date.today().year - 4   # 펀더멘털은 비어 5년 백필
    assert info["fund_ok"] == 7
    assert flow_calls["n"] == 0                              # 수급은 신규 거래일 없어 생략(per-ticker 미호출)


def test_index_members_steps_back_when_today_empty():
    # 오늘자(deposit file)가 빈 응답 → 가장 가까운 발행 영업일로 되짚어 구성종목을 얻는다.
    from etl.universe import index_members

    class FakeStock:
        def get_index_portfolio_deposit_file(self, code, date):
            # 6/3(수)·6/2(화)는 빈 응답, 6/1(월)에 구성종목 발행됨
            return ["005930", "000660"] if date == "20260601" else []

    out = index_members("1028", on=date(2026, 6, 3), stock=FakeStock())
    assert out == ["005930", "000660"]


def test_index_members_handles_dataframe_and_filters():
    # pykrx가 DataFrame(행 인덱스=종목코드)을 줘도, 비6자리/지수명은 걸러 코드만.
    import pandas as pd
    from etl.universe import index_members

    class FakeStock:
        def get_index_portfolio_deposit_file(self, code, date):
            return pd.DataFrame(index=["005930", "035720", "KOSPI200", "12345"])

    out = index_members("1028", on=date(2026, 6, 3), stock=FakeStock())
    assert out == ["005930", "035720"]  # 6자리만


def test_index_members_empty_when_all_blank():
    from etl.universe import index_members

    class FakeStock:
        def get_index_portfolio_deposit_file(self, code, date):
            return []

    assert index_members("1028", on=date(2026, 6, 3), stock=FakeStock()) == []


class _CountingStock:
    """구성종목 조회 횟수를 세는 가짜 — 캐시 적중 시 재조회가 없음을 검증."""
    def __init__(self, members):
        self.calls = 0
        self.members = members
    def get_index_portfolio_deposit_file(self, code, date):
        self.calls += 1
        return list(self.members)


def test_index_members_cached_hits_disk_and_skips_refetch(tmp_path):
    from etl.universe import index_members_cached, _index_cache_path
    s = _CountingStock(["005930", "000660"])
    on = date(2026, 6, 3)
    out1 = index_members_cached("KOSPI200", tmp_path, on=on, stock=s)
    assert out1 == ["005930", "000660"] and s.calls == 1
    assert _index_cache_path(tmp_path).exists()
    # 같은 날 재호출 → 디스크 캐시 적중, KRX 재조회 없음
    out2 = index_members_cached("KOSPI200", tmp_path, on=on, stock=s)
    assert out2 == ["005930", "000660"] and s.calls == 1


def test_index_members_cached_refetches_when_stale(tmp_path):
    from etl.universe import index_members_cached
    s = _CountingStock(["005930"])
    index_members_cached("KOSPI200", tmp_path, on=date(2026, 6, 3), stock=s)
    assert s.calls == 1
    # 8일 뒤(기본 max_age_days=7 초과) → 재조회
    index_members_cached("KOSPI200", tmp_path, on=date(2026, 6, 11), stock=s)
    assert s.calls == 2
    # 갱신된 캐시(6/11) 기준 7일 이내 → 다시 적중
    index_members_cached("KOSPI200", tmp_path, on=date(2026, 6, 12), stock=s)
    assert s.calls == 2


def test_index_members_cached_does_not_cache_empty(tmp_path):
    from etl.universe import index_members_cached, _index_cache_path
    s = _CountingStock([])  # 빈 결과 → 캐시 오염 방지(파일 미생성)
    assert index_members_cached("KOSPI200", tmp_path, on=date(2026, 6, 3), stock=s) == []
    assert not _index_cache_path(tmp_path).exists()


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
