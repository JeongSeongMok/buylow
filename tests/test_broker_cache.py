"""브로커 메모리 캐시 단위 테스트 — 백그라운드 스레드 없이 refresh/get/invalidate 직접 검증."""

from orchestrator.broker_cache import BrokerCache


class FakeBroker:
    def __init__(self):
        self.balance_calls = 0
        self.trade_calls = 0
    def balance(self):
        self.balance_calls += 1
        return {"deposit": 500000000, "items": []}
    def trades(self, date_iso):
        self.trade_calls += 1
        return [{"ts": f"{date_iso}T10:00:00", "ticker": "005930", "side": "BUY",
                 "qty": 1, "price": 71000, "amount": 71000, "realized_pnl": None, "reason": "x"}]


def test_cache_refresh_and_get_balance():
    fb = FakeBroker()
    c = BrokerCache(lambda: (fb, None))
    # 캐시 비었을 때 get_balance가 동기 1회 갱신
    bal, err, at = c.get_balance()
    assert bal["deposit"] == 500000000 and err is None and at
    assert fb.balance_calls == 1
    # 이후 조회는 캐시(추가 호출 없음)
    c.get_balance()
    assert fb.balance_calls == 1


def test_cache_get_trades_caches_per_date():
    fb = FakeBroker()
    c = BrokerCache(lambda: (fb, None))
    rows, at = c.get_trades("2026-06-05")
    assert len(rows) == 1 and at and fb.trade_calls == 1
    c.get_trades("2026-06-05")  # 같은 날짜 → 캐시
    assert fb.trade_calls == 1
    c.get_trades("2026-06-04")  # 다른 날짜 → 1회 더
    assert fb.trade_calls == 2


def test_cache_broker_missing_records_error():
    c = BrokerCache(lambda: (None, "키 없음"))
    bal, err, at = c.get_balance()
    assert bal is None and err == "키 없음"
    rows, at2 = c.get_trades("2026-06-05")
    assert rows is None


def test_cache_invalidate_forces_refresh():
    fb = FakeBroker()
    c = BrokerCache(lambda: (fb, None))
    c.get_balance(); assert fb.balance_calls == 1
    c.invalidate()
    c.get_balance(); assert fb.balance_calls == 2  # 무효화 후 다시 조회
