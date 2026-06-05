"""라이브 자동매매 프로세스 매니저 — 매매 탭 토글로 LEAN 라이브를 시작/중지(킬 스위치).

매매 탭에서 자동매매를 켜면 저장된 전략 + 라이브 유니버스로 LEAN 라이브 프로세스를 백그라운드
잡으로 spawn하고, 끄면 그 프로세스를 종료한다(killable). v1은 **계좌당 1 전략**(이미 돌고 있으면
재시작하지 않는다 — docs/ARCHITECTURE.md '라이브 멀티전략 보류').

`runner.run_live(req, proc_sink=...)`가 Popen 직후 proc 핸들을 넘겨주므로, 그걸 보관했다가
stop()에서 terminate()(5초 후 kill())한다. run_live는 프로세스가 끝날 때까지 blocking이라
JobManager 스레드에서 돌리고, terminate하면 wait가 풀려 잡이 정상 종료된다.
"""

from __future__ import annotations

import subprocess
import threading


class LiveProcessManager:
    def __init__(self, jobs):
        self._jobs = jobs
        self._lock = threading.Lock()
        self._proc: subprocess.Popen | None = None
        self._job_id: str | None = None

    def is_running(self) -> bool:
        with self._lock:
            return self._proc is not None and self._proc.poll() is None

    def _set_proc(self, proc) -> None:
        with self._lock:
            self._proc = proc

    def start(self, runner, request) -> str | None:
        """라이브 프로세스를 백그라운드로 시작. 이미 돌고 있으면 None(무시), 아니면 잡 id."""
        if self.is_running():
            return None

        def _job(job):
            def on_start(run_id, log_path):
                job.run_id = run_id
                job.log_path = str(log_path)
            return runner.run_live(request, on_start=on_start, proc_sink=self._set_proc)

        job = self._jobs.submit("라이브 자동매매", _job)
        with self._lock:
            self._job_id = job.id
        return job.id

    def stop(self) -> bool:
        """라이브 프로세스 종료(킬 스위치). 종료했으면 True."""
        with self._lock:
            proc = self._proc
            self._proc = None
            self._job_id = None
        if proc is None or proc.poll() is not None:
            return False
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        return True

    def status(self) -> dict:
        with self._lock:
            return {"running": self._proc is not None and self._proc.poll() is None,
                    "job_id": self._job_id}
