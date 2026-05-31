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


def test_empty_ticker(tmp_path):
    s = catalog.ticker_summary(tmp_path, "000000")
    assert s["price"]["count"] == 0 and s["flow"]["count"] == 0


def test_top_universe_reads_rank(tmp_path):
    assert catalog.top_universe(tmp_path, 10) == []  # 랭킹 파일 없으면 빈 리스트
    rank = tmp_path / "krx" / "universe_rank.csv"
    rank.parent.mkdir(parents=True, exist_ok=True)
    rank.write_text("005930,9000000\n000660,5000000\n035720,1000000\n", encoding="utf-8")
    assert catalog.top_universe(tmp_path, 2) == ["005930", "000660"]  # 거래대금 상위 2개
    assert catalog.top_universe(tmp_path, 99) == ["005930", "000660", "035720"]
