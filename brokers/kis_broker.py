"""KIS(한국투자증권) 매매 조회 어댑터 — 매매 탭 읽기 계층.

KisClient(REST)를 감싸 계좌/잔고/장상태를 대시보드용 dict로 돌려준다. 실주문은 LEAN 라이브 +
C# 어댑터(adapter/MyTrading.Kis)가 집행하고, 이 클래스는 '조회'만 한다(읽기/쓰기 분리).

라이브 환경(real/demo)은 config.get_live_config()의 env를 따른다 — 모의 계좌는 모의 URL/TR로만
조회되기 때문이다. 계좌번호·키는 config의 KIS 시크릿에서 가져온다.
"""

from __future__ import annotations

from datetime import datetime, time, date
from zoneinfo import ZoneInfo

from .base import mask_account
from .kis import KisClient

_SEOUL = ZoneInfo("Asia/Seoul")
# KRX 정규장 09:00~15:30 (market/krx.py와 동일).
_OPEN = time(9, 0)
_CLOSE = time(15, 30)


class KisBroker:
    name = "kis"
    label = "한국투자증권 (KIS)"

    def __init__(self, app_key: str, app_secret: str, account_no: str, env: str = "demo",
                 client: KisClient | None = None, now_fn=None):
        self._account_no = account_no or ""
        parts = self._account_no.split("-")
        self._cano = parts[0].strip() if parts and parts[0] else ""
        self._acnt_prdt_cd = parts[1].strip() if len(parts) > 1 and parts[1] else "01"
        self._env = env if env in ("real", "demo") else "demo"
        # 주입형 client(테스트) 우선, 없으면 env에 맞춰 생성.
        self._client = client or KisClient(app_key, app_secret, env=self._env)
        # 시각 주입(테스트 결정론). 기본은 서울 현재시각.
        self._now = now_fn or (lambda: datetime.now(_SEOUL))

    def account_info(self) -> dict:
        return {
            "broker": self.name,
            "broker_label": self.label,
            "account_no": mask_account(self._account_no),
            "account_type": "종합매매",
            "env": self._env,
        }

    def balance(self) -> dict:
        b = self._client.fetch_balance(self._cano, self._acnt_prdt_cd)
        total_purchase = sum(h["avg_price"] * h["qty"] for h in b["holdings"])
        total_pnl = sum(h["pnl"] for h in b["holdings"])
        total_pnl_pct = (total_pnl / total_purchase * 100) if total_purchase else 0.0
        # 가용현금(매수가능)은 D+2 예수금을 우선(결제반영). 없으면 총예수금.
        buying_power = b["d2_deposit"] or b["deposit"]
        return {
            "deposit": b["deposit"],
            "buying_power": buying_power,
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
            # 휴장일 조회 실패 시 주말만이라도 판정(보수적: 평일이면 시각으로).
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
            "env": self._env,
            "as_of": now.strftime("%Y-%m-%d %H:%M"),
        }


def get_trading_broker():
    """현재 설정으로 매매 조회 브로커를 만든다. 자격증명/계좌 미설정이면 (None, 사유)."""
    from orchestrator import config
    broker = config.get_broker()
    if broker != "kis":
        return None, f"'{broker}' 매매 조회는 아직 미지원입니다(KIS만 가능)."
    cred = config.get_kis_credentials()
    if not (cred["app_key"] and cred["app_secret"]):
        return None, "KIS App Key/Secret을 설정 탭에서 먼저 입력하세요."
    if not cred["account_no"]:
        return None, "KIS 계좌번호를 설정 탭에서 먼저 입력하세요."
    env = config.get_live_config()["env"]
    return KisBroker(cred["app_key"], cred["app_secret"], cred["account_no"], env=env), None
