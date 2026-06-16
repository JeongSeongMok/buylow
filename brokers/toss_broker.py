"""토스증권(Toss) 매매 조회 어댑터 — 매매 탭 읽기 계층.

TossClient(REST)를 감싸 계좌/잔고/장상태를 대시보드용 dict로 돌려준다. 실주문은 LEAN 라이브 +
C# 어댑터(adapter/MyTrading.Toss)가 집행하고, 이 클래스는 '조회'만 한다(읽기/쓰기 분리 — KisBroker와 동일).

KisBroker와의 차이:
- 모의투자 env가 없다(실전 단일).
- **trades(체결내역) 메서드가 없다** — Toss는 종료(CLOSED) 주문 조회를 아직 미지원하므로 계좌
  체결내역을 줄 수 없다. 매매 탭은 이 메서드가 없으면 buylow 자체 거래로그(TradeStore)로 폴백한다
  (brokers/base.py 설계). 메서드를 일부러 두지 않아 BrokerCache가 폴백 경로를 타게 한다.
"""

from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo

from .base import mask_account
from .toss import TossClient

_SEOUL = ZoneInfo("Asia/Seoul")
# KRX 정규장 09:00~15:30 (market/krx.py와 동일).
_OPEN = time(9, 0)
_CLOSE = time(15, 30)


class TossBroker:
    def __init__(self, client_id: str, client_secret: str,
                 client: TossClient | None = None, now_fn=None,
                 name: str = "toss", label: str = "토스증권"):
        self.name = name
        self.label = label
        # 주입형 client(테스트) 우선, 없으면 자격증명으로 생성.
        self._client = client or TossClient(client_id, client_secret)
        self._now = now_fn or (lambda: datetime.now(_SEOUL))

    def account_info(self) -> dict:
        # 계좌번호는 getAccounts로 해석(없으면 빈 문자열 → 마스킹이 '(미설정)').
        try:
            acct = self._client.account_no()
        except Exception:
            acct = ""
        return {
            "broker": self.name,
            "broker_label": self.label,
            "account_no": mask_account(acct),
            "account_type": "종합매매",
            "env": "real",  # Toss는 실전 단일
        }

    def balance(self) -> dict:
        b = self._client.fetch_balance()
        total_purchase = sum(h["avg_price"] * h["qty"] for h in b["holdings"])
        total_pnl = sum(h["pnl"] for h in b["holdings"])
        total_pnl_pct = (total_pnl / total_purchase * 100) if total_purchase else 0.0
        return {
            "deposit": b["deposit"],
            "buying_power": b["buying_power"],
            "total_eval": b["total_eval"],
            "total_purchase": total_purchase,
            "total_pnl": total_pnl,
            "total_pnl_pct": total_pnl_pct,
            "net_asset": b["net_asset"],
            "items": b["holdings"],
        }

    def market_status(self) -> dict:
        now = self._now()
        today = now.date()
        try:
            open_day = self._client.check_market_open(today)
        except Exception:
            # 휴장일 조회 실패 시 주말만이라도 판정(보수적; KisBroker와 동일 폴백).
            open_day = today.weekday() < 5
        if not open_day:
            session = "closed"
        elif now.time() < _OPEN:
            session = "pre"      # 장 시작 전
        elif now.time() <= _CLOSE:
            session = "regular"  # 장중
        else:
            session = "closed"   # 장 마감
        return {
            "open": session == "regular",
            "session": session,
            "is_holiday": not open_day,
            "env": "real",
            "as_of": now.strftime("%Y-%m-%d %H:%M"),
        }
