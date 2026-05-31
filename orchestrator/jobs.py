"""백그라운드 잡 매니저 — 오래 걸리는 작업(대량 ETL 등)을 요청과 분리해 실행/추적.

대시보드가 "최초 3년치 적재" 같은 장시간 작업을 트리거하면 HTTP 응답을 막지 않도록 별도
스레드에서 돌리고 상태(대기/실행/성공/실패)를 추적한다. 인메모리(재시작 시 초기화) — 단순/충분.
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


@dataclass
class Job:
    id: str
    name: str
    seq: int = 0                      # 생성 순서(정렬용; created_at은 초 단위라 동률 가능)
    state: str = "pending"            # pending | running | succeeded | failed
    created_at: str = field(default_factory=_now)
    finished_at: str | None = None
    result: str | None = None
    error: str | None = None


class JobManager:
    def __init__(self):
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._seq = 0

    def submit(self, name: str, fn: Callable[[], Any]) -> Job:
        with self._lock:
            self._seq += 1
            job = Job(id=uuid.uuid4().hex[:12], name=name, seq=self._seq)
            self._jobs[job.id] = job
        threading.Thread(target=self._run, args=(job, fn), daemon=True).start()
        return job

    def _run(self, job: Job, fn: Callable[[], Any]) -> None:
        job.state = "running"
        try:
            result = fn()
            job.result = str(result)[:1000]
            job.state = "succeeded"
        except Exception as e:  # 실패도 상태로 보존 (대시보드 표시)
            job.error = f"{type(e).__name__}: {e}"
            job.state = "failed"
        finally:
            job.finished_at = _now()

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def list(self) -> list[Job]:
        with self._lock:
            return sorted(self._jobs.values(), key=lambda j: j.seq, reverse=True)
