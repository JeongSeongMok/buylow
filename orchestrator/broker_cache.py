"""브로커 잔고·체결 메모리 캐시 + 백그라운드 리프레셔.

매매 탭이 화면을 그릴 때마다 KIS API(잔고·체결조회)를 동기로 기다리면 느리다. 그래서 서버가
켜져 있는 동안 **백그라운드 스레드가 주기적으로 활성 증권사의 잔고 + 당일 체결을 받아 메모리에
캐시**하고, 라우트는 그 캐시를 즉시 반환한다(KIS 왕복 없음 → 화면 즉시).

- 활성 증권사는 매 갱신마다 다시 본다(설정에서 바꾸면 다음 주기에 자동 반영; 즉시 반영은 invalidate).
- 매매내역은 '오늘'만 백그라운드로 갱신하고, 과거 날짜는 요청 시 한 번 조회해 캐시한다.
- 키 미설정/네트워크 실패는 캐시에 에러로 보관(화면이 표시).
- 캐시가 아직 비어 있으면(첫 진입) 라우트가 동기 1회 갱신으로 메운다.
"""

from __future__ import annotations

import threading
from datetime import datetime
from zoneinfo import ZoneInfo

_SEOUL = ZoneInfo("Asia/Seoul")


class BrokerCache:
    def __init__(self, get_broker, interval: float = 10.0):
        self._get_broker = get_broker
        self._interval = float(interval)
        self._lock = threading.Lock()
        self._balance = None
        self._balance_err = None
        self._balance_at = None
        self._trades: dict[str, tuple] = {}   # date_iso -> (rows, at)
        self._stop = threading.Event()
        self._thread = None

    # ── 백그라운드 스레드 ───────────────────────────────────────────────
    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, name="broker-cache", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.refresh()
            except Exception:
                pass  # 어떤 예외든 스레드는 죽지 않게
            self._stop.wait(self._interval)

    @staticmethod
    def _now() -> str:
        return datetime.now(_SEOUL).strftime("%H:%M:%S")

    @staticmethod
    def _today() -> str:
        return datetime.now(_SEOUL).date().isoformat()

    # ── 갱신 ────────────────────────────────────────────────────────────
    def refresh(self) -> None:
        """활성 증권사의 잔고 + 당일 체결을 받아 캐시. (예외는 에러로 보관.)"""
        broker, err = self._get_broker()
        if broker is None:
            with self._lock:
                self._balance, self._balance_err = None, err
            return
        now = self._now()
        try:
            bal = broker.balance()
            with self._lock:
                self._balance, self._balance_err, self._balance_at = bal, None, now
        except Exception as e:
            with self._lock:
                self._balance_err = f"{type(e).__name__}: {e}"
        if hasattr(broker, "trades"):
            day = self._today()
            try:
                rows = broker.trades(day)
                with self._lock:
                    self._trades[day] = (rows, now)
            except Exception:
                pass

    def invalidate(self) -> None:
        """증권사/키 변경 시 캐시 비움 — 다음 조회가 새 활성 증권사로 즉시 채운다."""
        with self._lock:
            self._balance, self._balance_err, self._balance_at = None, None, None
            self._trades = {}

    # ── 조회(라우트용) ──────────────────────────────────────────────────
    def get_balance(self) -> tuple:
        """(balance, err, at). 캐시가 비어 있으면(첫 진입) 동기 1회 갱신."""
        with self._lock:
            bal, err, at = self._balance, self._balance_err, self._balance_at
        if bal is None and err is None:
            self.refresh()
            with self._lock:
                bal, err, at = self._balance, self._balance_err, self._balance_at
        return bal, err, at

    def get_trades(self, date_iso: str) -> tuple:
        """(rows, at). 캐시 우선, 미스(과거 날짜 등)면 활성 증권사로 1회 조회해 캐시. 실패 시 (None, None)."""
        with self._lock:
            v = self._trades.get(date_iso)
        if v is not None:
            return v
        broker, _err = self._get_broker()
        if broker is None or not hasattr(broker, "trades"):
            return None, None
        try:
            rows = broker.trades(date_iso)
        except Exception:
            return None, None
        at = self._now()
        with self._lock:
            self._trades[date_iso] = (rows, at)
        return rows, at
