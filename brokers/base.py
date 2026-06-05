"""브로커 무관 매매 조회 인터페이스 (매매 탭 읽기 계층).

대시보드 매매 탭에는 **KIS와 Toss가 모두 제공하는 기능의 교집합만** 노출한다(나중에 Toss를 붙여도
UI/계약을 바꾸지 않게). MCP 가이드로 확인한 교집합:
  - 계좌 정보(증권사/계좌/실전·모의)         — KIS config / Toss getAccounts
  - 예수금 + 보유종목(매수가/현재가/손익)     — KIS inquire-balance / Toss getHoldings+getBuyingPower
  - 장 운영 상태(장중/장마감/휴장)            — KIS chk-holiday+시각 / Toss market-calendar
교집합에서 빠지는 것: '매매 내역(종료 주문)' — Toss가 CLOSED 조회를 아직 미지원하므로 브로커 API가
아니라 buylow 자체 거래로그(TradeStore)를 SoR로 쓴다(브로커 무관).

각 브로커 어댑터는 아래 TradingBroker 형태를 따른다(덕타이핑). 실패는 호출부(라우트)가 잡아
섹션별로 우아하게 처리한다.
"""

from __future__ import annotations

from typing import Protocol


def mask_account(account_no: str | None) -> str:
    """계좌번호 일부 마스킹(화면 표시용). 예: '12345678-01' → '1234****-01'."""
    if not account_no:
        return "(미설정)"
    s = str(account_no)
    if "-" in s:
        head, tail = s.split("-", 1)
        head_m = head[:4] + "*" * max(0, len(head) - 4)
        return f"{head_m}-{tail}"
    return s[:4] + "*" * max(0, len(s) - 4)


class TradingBroker(Protocol):
    """매매 탭이 의존하는 최소 인터페이스(KIS∩Toss 교집합). 반환은 평범한 dict."""

    name: str        # "kis" | "toss"
    label: str       # 화면 표시명

    def account_info(self) -> dict:
        """{broker, broker_label, account_no(마스킹), account_type, env}."""
        ...

    def balance(self) -> dict:
        """{deposit, buying_power, total_eval, total_purchase, total_pnl, total_pnl_pct,
        items: [{ticker,name,qty,avg_price,cur_price,eval_amount,pnl,pnl_pct}]}."""
        ...

    def market_status(self) -> dict:
        """{open, session, is_holiday, env, as_of}."""
        ...

    def trades(self, date_iso: str) -> list[dict]:
        """해당 날짜의 계좌 체결내역(선택 — 지원 브로커만). 미지원이면 buylow 자체 거래로그로 폴백.
        [{ts,ticker,name,side,qty,price,amount,realized_pnl,reason}]. (KIS는 체결조회로 실거래 반영)"""
        ...
