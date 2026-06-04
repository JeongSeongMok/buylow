"""체결 거래로그 저장소 (SQLite) — 매매 탭 'C. 매매 내역'의 시스템 오브 레코드.

왜 브로커 API가 아니라 자체 기록인가: 매매 내역(종료/체결 주문)을 날짜별로 보려면 종료 주문 조회가
필요한데, Toss Open API는 현재 종료 주문(CLOSED) 조회를 미지원한다(OPEN만). KIS는 되지만 두
증권사 교집합으로만 대시보드를 구성하기로 했으므로, buylow가 직접 낸 주문/체결을 자체 로그로
보존해 브로커 무관하게 보여준다. 라이브 엔진(KisBrokerage)의 체결 이벤트가 이 테이블에 적재된다.

설계는 RunStore와 동일(WAL, 연산마다 커넥션 open/close — 개인용 로컬이라 동시성 낮음)."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS trades (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            TEXT NOT NULL,    -- 체결 시각 ISO8601
    trade_date    TEXT NOT NULL,    -- YYYY-MM-DD (날짜별 조회 키)
    ticker        TEXT NOT NULL,
    name          TEXT,
    side          TEXT NOT NULL,    -- BUY | SELL
    qty           INTEGER NOT NULL,
    price         REAL NOT NULL,    -- 체결단가(원)
    amount        REAL NOT NULL,    -- 체결금액(원) = price*qty
    realized_pnl  REAL,             -- 실현손익(매도 시); 없으면 NULL
    reason        TEXT,             -- 전략 신호/리스크 사유
    broker        TEXT,             -- kis | toss
    env           TEXT,             -- real | demo
    order_no      TEXT,             -- 브로커 주문번호(ODNO 등)
    session_id    TEXT              -- 라이브 세션/런 id
);
CREATE INDEX IF NOT EXISTS idx_trades_date ON trades(trade_date);
"""


def default_trade_db_path() -> Path:
    """기본 DB 위치(RunStore와 같은 파일을 공유해도 무방하지만 분리 운영도 가능)."""
    return REPO_ROOT / "buylow.db"


class TradeStore:
    """체결 거래로그 — 날짜별 조회 + 일별 실현손익 집계."""

    def __init__(self, db_path: str | Path | None = None):
        self.db_path = str(db_path or default_trade_db_path())
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def record_trade(self, trade: dict[str, Any]) -> int:
        """체결 1건 기록. side는 'BUY'/'SELL'. amount 미지정 시 price*qty로 계산. id 반환."""
        ts = trade.get("ts") or datetime.now().isoformat(timespec="seconds")
        trade_date = trade.get("trade_date") or ts[:10]
        qty = int(trade.get("qty", 0))
        price = float(trade.get("price", 0) or 0)
        amount = trade.get("amount")
        amount = float(amount) if amount is not None else price * qty
        row = {
            "ts": ts, "trade_date": trade_date,
            "ticker": trade.get("ticker", ""), "name": trade.get("name", ""),
            "side": (trade.get("side") or "BUY").upper(),
            "qty": qty, "price": price, "amount": amount,
            "realized_pnl": trade.get("realized_pnl"),
            "reason": trade.get("reason", ""), "broker": trade.get("broker", ""),
            "env": trade.get("env", ""), "order_no": trade.get("order_no", ""),
            "session_id": trade.get("session_id", ""),
        }
        with self._connect() as conn:
            cur = conn.execute(
                """INSERT INTO trades
                   (ts, trade_date, ticker, name, side, qty, price, amount,
                    realized_pnl, reason, broker, env, order_no, session_id)
                   VALUES (:ts,:trade_date,:ticker,:name,:side,:qty,:price,:amount,
                           :realized_pnl,:reason,:broker,:env,:order_no,:session_id)""",
                row,
            )
            return int(cur.lastrowid)

    def list_trades(self, trade_date: str) -> list[dict[str, Any]]:
        """해당 날짜(YYYY-MM-DD)의 체결 내역(시간순)."""
        with self._connect() as conn:
            cur = conn.execute(
                "SELECT * FROM trades WHERE trade_date = ? ORDER BY ts, id", (trade_date,))
            return [dict(r) for r in cur.fetchall()]

    def trade_dates(self) -> list[str]:
        """체결이 있는 날짜 목록(오름차순) — 날짜 화살표 이동용."""
        with self._connect() as conn:
            cur = conn.execute("SELECT DISTINCT trade_date FROM trades ORDER BY trade_date")
            return [r[0] for r in cur.fetchall()]

    def adjacent_date(self, trade_date: str, direction: int) -> str | None:
        """trade_date 기준 이전(-1)/다음(+1) 거래 기록이 있는 날짜. 없으면 None."""
        op = "<" if direction < 0 else ">"
        order = "DESC" if direction < 0 else "ASC"
        with self._connect() as conn:
            cur = conn.execute(
                f"SELECT trade_date FROM trades WHERE trade_date {op} ? "
                f"ORDER BY trade_date {order} LIMIT 1", (trade_date,))
            r = cur.fetchone()
            return r[0] if r else None

    def daily_pnl(self, trade_date: str) -> float:
        """해당 날짜의 실현손익 합(매도 체결의 realized_pnl 합)."""
        with self._connect() as conn:
            cur = conn.execute(
                "SELECT COALESCE(SUM(realized_pnl), 0) FROM trades WHERE trade_date = ?",
                (trade_date,))
            return float(cur.fetchone()[0] or 0)
