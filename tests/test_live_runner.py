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
