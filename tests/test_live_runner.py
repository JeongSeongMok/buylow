"""LiveProcessManager 단위 테스트 — 가짜 runner/proc로 start→is_running→stop(킬) 검증."""

from orchestrator.live_runner import LiveProcessManager
from orchestrator.jobs import JobManager
import time


class FakeProc:
    def __init__(self):
        self._alive = True
        self.terminated = False
    def poll(self):
        return None if self._alive else 0
    def terminate(self):
        self.terminated = True; self._alive = False
    def wait(self, timeout=None):
        return 0
    def kill(self):
        self._alive = False


class FakeRunner:
    """run_live가 proc_sink로 proc를 넘기고, terminate될 때까지 사는 것을 흉내."""
    def __init__(self, proc):
        self.proc = proc
        self.called = False
    def run_live(self, request, on_start=None, proc_sink=None):
        self.called = True
        if on_start:
            on_start("live-x", "/tmp/x.log")
        if proc_sink:
            proc_sink(self.proc)
        # 프로세스가 종료(terminate)될 때까지 블로킹하는 것을 흉내
        while self.proc.poll() is None:
            time.sleep(0.01)
        return None


def _wait(cond, timeout=2.0):
    end = time.time() + timeout
    while time.time() < end and not cond():
        time.sleep(0.01)
    return cond()


def test_start_then_stop_kills_process():
    jobs = JobManager()
    mgr = LiveProcessManager(jobs)
    proc = FakeProc()
    runner = FakeRunner(proc)
    assert mgr.is_running() is False
    mgr.start(runner, object())
    assert _wait(lambda: mgr.is_running())   # 백그라운드 잡이 proc_sink로 등록할 때까지
    assert runner.called
    assert mgr.stop() is True
    assert proc.terminated is True
    assert mgr.is_running() is False


def test_start_ignored_when_already_running():
    jobs = JobManager()
    mgr = LiveProcessManager(jobs)
    proc = FakeProc()
    mgr.start(FakeRunner(proc), object())
    assert _wait(lambda: mgr.is_running())
    # 이미 실행 중이면 두 번째 start는 무시(None)
    assert mgr.start(FakeRunner(FakeProc()), object()) is None
    mgr.stop()


def test_stop_when_not_running_returns_false():
    mgr = LiveProcessManager(JobManager())
    assert mgr.stop() is False


# ── 워치독(감독 스레드) — 운영 안정성 ──────────────────────────────────────
class DyingRunner:
    """run_live가 proc를 잠깐 살리고 스스로 죽는 것(크래시)을 흉내. 호출 횟수를 센다."""
    def __init__(self, life=0.03):
        self.calls = 0
        self.life = life
    def run_live(self, request, on_start=None, proc_sink=None):
        self.calls += 1
        proc = FakeProc()
        if on_start:
            on_start(f"live-{self.calls}", "/tmp/x.log")
        if proc_sink:
            proc_sink(proc)
        time.sleep(self.life)
        proc._alive = False   # 크래시(엔진 종료) 흉내
        return None


def test_watchdog_restarts_after_crash():
    # desired=ON인 채 프로세스가 죽으면 감독이 백오프 후 자동 재시작해야 한다.
    mgr = LiveProcessManager(JobManager(), poll_interval=0.02, base_backoff=0.02, stable_secs=999)
    runner = DyingRunner(life=0.03)
    mgr.enable(runner, lambda: object())
    assert _wait(lambda: runner.calls >= 3, timeout=4.0)   # 죽을 때마다 재시작(≥3회)
    mgr.disable()


def test_disable_stops_and_no_restart():
    mgr = LiveProcessManager(JobManager(), poll_interval=0.02, base_backoff=0.02)
    proc = FakeProc()
    mgr.enable(FakeRunner(proc), lambda: object())
    assert _wait(lambda: mgr.is_running())
    assert mgr.disable() is True
    assert proc.terminated is True
    # disable 후엔(desired=OFF) 감독이 재시작하지 않는다.
    time.sleep(0.1)
    assert mgr.is_running() is False


def test_failing_build_records_error_and_does_not_run():
    # build_request가 실패하면(전략/유니버스 미비) 라이브는 안 뜨고 사유만 남는다.
    class NeverRuns:
        def run_live(self, *a, **k):
            raise AssertionError("build 실패 시 run_live가 호출되면 안 됨")
    def bad_build():
        raise RuntimeError("전략 미저장")
    mgr = LiveProcessManager(JobManager(), poll_interval=0.05, base_backoff=10.0)
    mgr.enable(NeverRuns(), bad_build)
    assert _wait(lambda: mgr.status()["last_error"] is not None, timeout=2.0)
    assert mgr.is_running() is False
    mgr.disable()


# ── build_live_request — 최신 config로 라이브 spec 구성 ──────────────────────
def test_build_live_request_requires_strategy_and_universe():
    import pytest
    from orchestrator import config
    from orchestrator.live_runner import build_live_request

    with pytest.raises(RuntimeError):       # 전략 없음
        build_live_request()
    config.save_strategy({"signals": [], "resolution": "daily"})
    with pytest.raises(RuntimeError):       # 유니버스 없음
        build_live_request()


def test_build_live_request_embeds_universe_and_resolves_data_folder():
    import json
    from pathlib import Path
    from orchestrator import config
    from orchestrator.live_runner import build_live_request

    config.save_strategy({"signals": [], "resolution": "daily"})
    config.save_live_universe(["005930", "000660"])
    req = build_live_request()
    assert req.resolved_algorithm_type() == "RuleStrategy"
    spec = json.loads(req.parameters["rule_spec"])
    assert spec["universe"] == ["005930", "000660"]
    # 분봉 가용성 판정이 절대경로를 요구하므로 data_folder는 resolve된 절대경로여야 한다.
    assert Path(spec["data_folder"]).is_absolute()
    # 라이브는 start/end/cash 미설정(현재시각·계좌잔액).
    assert "start" not in spec and "end" not in spec
