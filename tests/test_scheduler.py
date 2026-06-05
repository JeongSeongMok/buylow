"""자동 적재 스케줄러 단위 테스트 — 가짜 JobManager로 틱이 던지는 잡을 검증(실제 적재/네트워크 없음)."""

from orchestrator import scheduler


class FakeJobs:
    def __init__(self):
        self.names = []

    def submit(self, name, fn):
        self.names.append(name)
        return type("J", (), {"id": "x"})()


def test_disabled_returns_none(monkeypatch):
    monkeypatch.setattr(scheduler, "get_scheduler_config",
                        lambda: {"enabled": False, "interval_minutes": 30, "minute_universe": []})
    assert scheduler.start_scheduler(FakeJobs()) is None


def test_tick_daily_only_when_no_minute_universe(monkeypatch):
    monkeypatch.setattr(scheduler, "get_data_folder", lambda: "/tmp/data")
    monkeypatch.setattr(scheduler, "get_scheduler_config",
                        lambda: {"enabled": True, "interval_minutes": 30, "minute_universe": []})
    jobs = FakeJobs()
    scheduler.run_scheduled(jobs)
    # 분봉 대상이 없으면 일봉만 던진다
    assert jobs.names == ["데이터 최신화 (자동)"]


def test_tick_includes_minute_when_universe_set(monkeypatch):
    monkeypatch.setattr(scheduler, "get_data_folder", lambda: "/tmp/data")
    monkeypatch.setattr(scheduler, "get_scheduler_config",
                        lambda: {"enabled": True, "interval_minutes": 30,
                                 "minute_universe": ["005930", "000660"]})
    jobs = FakeJobs()
    scheduler.run_scheduled(jobs)
    assert jobs.names == ["데이터 최신화 (자동)", "분봉 최신화 (자동)"]


def test_start_scheduler_uses_interval(monkeypatch):
    monkeypatch.setattr(scheduler, "get_scheduler_config",
                        lambda: {"enabled": True, "interval_minutes": 15, "minute_universe": []})
    sched = scheduler.start_scheduler(FakeJobs())
    try:
        assert sched is not None
        job = sched.get_job("auto-data-update")
        assert job is not None
        # IntervalTrigger의 간격이 설정값(15분=900초)과 일치
        assert job.trigger.interval.total_seconds() == 15 * 60
    finally:
        sched.shutdown(wait=False)
