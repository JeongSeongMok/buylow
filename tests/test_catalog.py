"""데이터 카탈로그(읽기) 단위 테스트 — 샘플 적재 후 되읽기 검증."""

from datetime import date

from etl import catalog
from etl.flow import FlowRecord, write_flow
from etl.lean_format import write_equity_daily
from etl.sources import Bar


def _seed(tmp_path):
    write_equity_daily(tmp_path, "krx", "005930", [
        Bar(date(2023, 1, 2), 55500, 56100, 55200, 55500, 10031448),
        Bar(date(2023, 1, 3), 55400, 56000, 54500, 55400, 13547030),
    ])
    write_flow(tmp_path, "005930", [
        FlowRecord(date(2023, 1, 2), -33675372900, -26810406100, 59089365900),
    ])


def test_lists_and_summary(tmp_path):
    _seed(tmp_path)
    assert catalog.all_tickers(tmp_path) == ["005930"]

    summary = catalog.ticker_summary(tmp_path, "005930")
    # 가격: ×10000 역스케일되어 실제가로 복원
    assert summary["price"]["count"] == 2
    assert summary["price"]["recent"][0]["close"] == 55400.0  # 최신순(1/3이 먼저)
    assert summary["price"]["first"] == "2023-01-02"
    # 수급
    assert summary["flow"]["count"] == 1
    assert summary["flow"]["recent"][0]["foreign"] == -33675372900


def test_minute_listing_and_count(tmp_path):
    from etl.lean_format import write_equity_minute
    from etl.sources import MinuteBar
    assert catalog.list_minute_tickers(tmp_path) == []
    assert catalog.minute_day_count(tmp_path, "005930") == 0
    bar = [MinuteBar(9 * 3600 * 1000, 100, 100, 100, 100, 1)]
    write_equity_minute(tmp_path, "krx", "005930", date(2026, 6, 1), bar)
    write_equity_minute(tmp_path, "krx", "005930", date(2026, 6, 2), bar)
    assert catalog.list_minute_tickers(tmp_path) == ["005930"]
    assert catalog.minute_day_count(tmp_path, "005930") == 2
    assert catalog.minute_latest_date(tmp_path, "005930") == "2026-06-02"  # 최신 적재일
    assert catalog.minute_latest_date(tmp_path, "000660") is None          # 분봉 없음
    assert "005930" in catalog.all_tickers(tmp_path)  # 분봉만 있어도 목록에 포함


def test_empty_ticker(tmp_path):
    s = catalog.ticker_summary(tmp_path, "000000")
    assert s["price"]["count"] == 0 and s["flow"]["count"] == 0


def test_latest_loaded_date(tmp_path):
    assert catalog.latest_loaded_date(tmp_path) is None  # 적재 없음
    _seed(tmp_path)  # 005930: 2023-01-02, 2023-01-03
    assert catalog.latest_loaded_date(tmp_path) == "2023-01-03"  # 최신 거래일
