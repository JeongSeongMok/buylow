"""KRX 시장 레이어(market.krx) 단위 테스트 — LEAN 없이 순수 로직 검증."""

import csv
import json
from decimal import Decimal

from market.krx import (
    KRX_MARKET, korean_fee, inject_krx_market,
    DEFAULT_COMMISSION_RATE, DEFAULT_SELL_TAX_RATE,
)


def test_buy_fee_is_commission_only():
    # 100주 × 70,000원 = 7,000,000원, 매수는 수수료만
    fee = korean_fee(70_000, 100)
    assert fee == Decimal("7000000") * DEFAULT_COMMISSION_RATE


def test_sell_fee_adds_transaction_tax():
    # 매도(음수)는 수수료 + 거래세
    fee = korean_fee(70_000, -100)
    value = Decimal("7000000")
    assert fee == value * (DEFAULT_COMMISSION_RATE + DEFAULT_SELL_TAX_RATE)


def test_sell_costs_more_than_buy():
    assert korean_fee(70_000, -100) > korean_fee(70_000, 100)


def test_inject_creates_market_config(tmp_path):
    inject_krx_market(tmp_path)

    mh = json.loads((tmp_path / "market-hours" / "market-hours-database.json").read_text())
    entry = mh["entries"][f"Equity-{KRX_MARKET}-[*]"]
    assert entry["exchangeTimeZone"] == "Asia/Seoul"
    # 평일 정규장 09:00~15:30
    assert entry["monday"][0]["start"] == "09:00:00"
    assert entry["monday"][0]["end"] == "15:30:00"
    assert entry["saturday"] == []

    with open(tmp_path / "symbol-properties" / "symbol-properties-database.csv") as f:
        rows = list(csv.reader(f))
    assert rows[0][0] == "market"  # 헤더
    krx_rows = [r for r in rows[1:] if r[0] == KRX_MARKET]
    assert len(krx_rows) == 1
    assert krx_rows[0][4] == "KRW"  # quote_currency


def test_inject_preserves_existing_and_is_idempotent(tmp_path):
    # 기존에 다른 시장 항목이 있어도 보존하고, 중복 주입해도 KRX 행은 하나만.
    mh_dir = tmp_path / "market-hours"
    mh_dir.mkdir()
    (mh_dir / "market-hours-database.json").write_text(
        json.dumps({"entries": {"Equity-usa-[*]": {"exchangeTimeZone": "America/New_York"}}})
    )

    inject_krx_market(tmp_path)
    inject_krx_market(tmp_path)  # 두 번

    mh = json.loads((mh_dir / "market-hours-database.json").read_text())
    assert "Equity-usa-[*]" in mh["entries"]          # 기존 보존
    assert f"Equity-{KRX_MARKET}-[*]" in mh["entries"]  # KRX 추가

    with open(tmp_path / "symbol-properties" / "symbol-properties-database.csv") as f:
        rows = list(csv.reader(f))
    krx_rows = [r for r in rows[1:] if r[0] == KRX_MARKET]
    assert len(krx_rows) == 1  # 멱등
