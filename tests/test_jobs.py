"""백그라운드 잡 매니저 단위 테스트."""

import time

from orchestrator.jobs import JobManager


def _wait(jm, job_id, timeout=2.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        j = jm.get(job_id)
        if j and j.state in ("succeeded", "failed"):
            return j
        time.sleep(0.01)
    return jm.get(job_id)


def test_job_succeeds_and_keeps_result():
    jm = JobManager()
    job = jm.submit("ok", lambda: {"ingested": 3})
    j = _wait(jm, job.id)
    assert j.state == "succeeded"
    assert "ingested" in j.result
    assert j.finished_at is not None


def test_job_failure_is_captured():
    def boom():
        raise ValueError("터짐")
    jm = JobManager()
    job = jm.submit("bad", boom)
    j = _wait(jm, job.id)
    assert j.state == "failed"
    assert "터짐" in j.error


def test_list_most_recent_first():
    jm = JobManager()
    a = jm.submit("a", lambda: 1)
    _wait(jm, a.id)
    b = jm.submit("b", lambda: 2)
    _wait(jm, b.id)
    names = [j.name for j in jm.list()]
    assert names[0] == "b"  # 최신 먼저
