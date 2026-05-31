"""KRX 가격 ETL 테스트 — 가짜 소스로 네트워크 없이 변환/적재 검증."""

import zipfile
from datetime import date

import pytest

from etl import krx
from etl.lean_format import PRICE_SCALE, equity_daily_zip_path, write_equity_daily
from etl.sources import Bar


def _bars():
    return [
        Bar(date(2023, 1, 2), 55500, 56100, 55200, 55500, 10031448),
        Bar(date(2023, 1, 3), 55400, 56000, 54500, 55400, 13547030),
    ]


def test_write_equity_daily_format(tmp_path):
    write_equity_daily(tmp_path, "krx", "005930", _bars())
    zp = equity_daily_zip_path(tmp_path, "krx", "005930")
    assert zp.exists()
    with zipfile.ZipFile(zp) as zf:
        lines = zf.read("005930.csv").decode().strip().splitlines()
    first = lines[0].split(",")
    assert first[0] == "20230102 00:00"
    assert int(first[4]) == 55500 * PRICE_SCALE  # 종가 ×10000 스케일
    assert int(first[5]) == 10031448             # 거래량
    assert (tmp_path / "equity" / "krx" / "map_files").is_dir()
    assert (tmp_path / "equity" / "krx" / "factor_files").is_dir()


def test_write_merge_appends_without_duplicates(tmp_path):
    from etl.lean_format import read_equity_daily
    write_equity_daily(tmp_path, "krx", "005930", _bars())  # 1/2, 1/3
    # 1/3(갱신) + 1/4(신규) 를 병합
    write_equity_daily(tmp_path, "krx", "005930", [
        Bar(date(2023, 1, 3), 60000, 60000, 60000, 60000, 1),
        Bar(date(2023, 1, 4), 57000, 57000, 57000, 57000, 999),
    ], merge=True)
    bars = read_equity_daily(tmp_path, "krx", "005930")
    days = [b.day for b in bars]
    assert days == [date(2023, 1, 2), date(2023, 1, 3), date(2023, 1, 4)]  # 중복 없음
    assert next(b for b in bars if b.day == date(2023, 1, 3)).close == 60000  # 새 값 우선


def test_write_sorts_by_date(tmp_path):
    write_equity_daily(tmp_path, "krx", "005930", list(reversed(_bars())))
    with zipfile.ZipFile(equity_daily_zip_path(tmp_path, "krx", "005930")) as zf:
        lines = zf.read("005930.csv").decode().strip().splitlines()
    assert lines[0].startswith("20230102") and lines[1].startswith("20230103")


class FakeSource:
    name = "fake"

    def __init__(self, bars):
        self._bars = bars

    def fetch_daily(self, ticker, start, end):
        return self._bars


def test_ingest_writes_and_injects_market(tmp_path):
    info = krx.ingest("005930", date(2023, 1, 1), date(2023, 1, 31), tmp_path, FakeSource(_bars()))
    assert info["bars"] == 2 and info["ticker"] == "005930"
    assert (tmp_path / "equity" / "krx" / "daily" / "005930.zip").exists()
    # KRX 시장설정도 함께 주입돼야 함
    assert (tmp_path / "market-hours" / "market-hours-database.json").exists()


def test_ingest_empty_raises(tmp_path):
    with pytest.raises(RuntimeError):
        krx.ingest("005930", date(2023, 1, 1), date(2023, 1, 2), tmp_path, FakeSource([]))


@pytest.mark.integration
def test_pykrx_real_fetch():
    """실제 pykrx OHLCV 조회 (무인증). 네트워크 필요 → 기본 실행 제외."""
    from etl.sources import get_source
    bars = get_source("pykrx").fetch_daily("005930", date(2023, 1, 2), date(2023, 1, 10))
    assert len(bars) >= 3
    assert all(b.close > 0 for b in bars)
