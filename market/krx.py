"""KRX(한국거래소) 시장 정의 — 시장 코드/통화/장시간/수수료의 단일 출처.

여기엔 .NET 의존 없는 순수 로직만 둔다:
  - 상수(시장 코드/식별자/통화)
  - 한국 수수료/거래세 계산 (korean_fee)
  - LEAN 데이터 폴더에 KRX 시장설정(market-hours·symbol-properties) 주입 (inject_krx_market)
LEAN 런타임에서 쓰는 KoreanFeeModel·KrxAlgorithm 베이스는 strategies/krx.py가 이 값을 import한다.

근거: KRX 백테스트는 C# 없이 Python+설정으로 충분(docs/ARCHITECTURE.md). C# 어댑터는 라이브 연결 전용.
"""

from __future__ import annotations

import csv
import json
from decimal import Decimal
from pathlib import Path

# --- 시장 상수 ---
KRX_MARKET = "krx"
KRX_MARKET_ID = 50          # Market.Add 식별자 (1~999, 기존과 충돌 없는 값)
KRX_CURRENCY = "KRW"
KRX_TIME_ZONE = "Asia/Seoul"

# --- 비용 모델 기본값 ---
# 매수: 위탁수수료만. 매도: 위탁수수료 + 증권거래세(농특세 포함 근사).
# 실제 수수료율은 증권사마다 다르므로 전략에서 조정 가능하게 기본값으로 둔다.
DEFAULT_COMMISSION_RATE = Decimal("0.00015")  # 0.015%
DEFAULT_SELL_TAX_RATE = Decimal("0.0018")     # 0.18% (매도 시에만)


def korean_fee(
    price: float,
    quantity: float,
    commission_rate: Decimal = DEFAULT_COMMISSION_RATE,
    sell_tax_rate: Decimal = DEFAULT_SELL_TAX_RATE,
) -> Decimal:
    """주문 1건의 한국식 비용(KRW). quantity 부호: 양수=매수, 음수=매도.

    매도에만 증권거래세를 더한다(한국 제도). 반환은 Decimal(통화=KRW).
    """
    value = Decimal(str(price)) * Decimal(abs(int(quantity)))
    fee = value * commission_rate
    if quantity < 0:  # 매도
        fee += value * sell_tax_rate
    return fee


def _market_hours_entry() -> dict:
    """KRX 정규장 09:00~15:30 (Asia/Seoul), 주말 휴장."""
    session = [{"start": "09:00:00", "end": "15:30:00", "state": "market"}]
    weekdays = {d: session for d in ("monday", "tuesday", "wednesday", "thursday", "friday")}
    return {
        "dataTimeZone": KRX_TIME_ZONE,
        "exchangeTimeZone": KRX_TIME_ZONE,
        "sunday": [],
        **weekdays,
        "saturday": [],
        "holidays": [],
        "earlyCloses": {},
        "lateOpens": {},
    }


_SYMBOL_PROPERTIES_HEADER = [
    "market", "symbol", "type", "description", "quote_currency",
    "contract_multiplier", "minimum_price_variation", "lot_size",
    "market_ticker", "minimum_order_size", "price_magnifier", "strike_multiplier",
]
# KRW는 소수점 없음 → 최소 호가단위 1, 1주 단위
_KRX_EQUITY_ROW = [KRX_MARKET, "[*]", "equity", "KRX Equity", KRX_CURRENCY,
                   "1", "1", "1", "", "", "1", ""]


def inject_krx_market(data_folder: str | Path) -> None:
    """LEAN 데이터 폴더에 KRX 시장설정을 주입(없으면 추가, 있으면 보존하며 병합).

    market-hours-database.json 과 symbol-properties-database.csv 에 KRX 항목을 넣는다.
    기존 항목(다른 시장)은 보존하므로 ETL이 만든 데이터 폴더에 안전하게 덧쓸 수 있다.
    """
    data_folder = Path(data_folder)

    # 1) market-hours
    mh = data_folder / "market-hours" / "market-hours-database.json"
    db = {"entries": {}}
    if mh.exists():
        db = json.loads(mh.read_text(encoding="utf-8"))
        db.setdefault("entries", {})
    db["entries"][f"Equity-{KRX_MARKET}-[*]"] = _market_hours_entry()
    mh.parent.mkdir(parents=True, exist_ok=True)
    mh.write_text(json.dumps(db, indent=2), encoding="utf-8")

    # 2) symbol-properties (헤더 + KRX equity 행, 중복 방지)
    sp = data_folder / "symbol-properties" / "symbol-properties-database.csv"
    rows: list[list[str]] = []
    if sp.exists():
        with open(sp, newline="", encoding="utf-8") as f:
            rows = [r for r in csv.reader(f) if r]
    if not rows or rows[0] != _SYMBOL_PROPERTIES_HEADER:
        rows = [_SYMBOL_PROPERTIES_HEADER] + [r for r in rows if r != _SYMBOL_PROPERTIES_HEADER]
    key = (_KRX_EQUITY_ROW[0], _KRX_EQUITY_ROW[1], _KRX_EQUITY_ROW[2])
    if not any((r[0], r[1], r[2]) == key for r in rows[1:]):
        rows.append(_KRX_EQUITY_ROW)
    sp.parent.mkdir(parents=True, exist_ok=True)
    with open(sp, "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows(rows)
