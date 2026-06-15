"""라이브 자동매매 프로세스 매니저 — 매매 탭 토글로 LEAN 라이브를 시작/중지(킬 스위치) + 감독.

매매 탭에서 자동매매를 켜면 저장된 전략 + 라이브 유니버스로 LEAN 라이브 프로세스를 백그라운드
잡으로 spawn하고, 끄면 그 프로세스를 종료한다(killable). v1은 **계좌당 1 전략**.

**운영 안정성(워치독):** `enable()`은 '원하는 상태(desired=ON)'를 기록하고 감독 스레드를 띄운다.
감독 스레드는 desired=ON인데 프로세스가 죽어 있으면 **백오프 후 자동 재시작**한다(장중 크래시·
일시 장애 복원). `disable()`은 desired=OFF로 두고 프로세스를 종료한다(킬 스위치 — 재시작 안 함).
재시작 때마다 `build_request()`를 다시 호출해 **최신 config(전략/유니버스)를 반영**한다.

`runner.run_live(req, proc_sink=...)`가 Popen 직후 proc 핸들을 넘겨주므로, 그걸 보관했다가
종료(terminate→5초 후 kill)한다. run_live는 프로세스가 끝날 때까지 blocking이라 JobManager
스레드에서 돌리고, terminate하면 wait가 풀려 잡이 정상 종료된다.

⚠️ 재시작 시 기존 미체결 주문 resync는 미구현(GetOpenOrders가 빈 목록). 다만 보유 포지션은 KIS
잔고조회(GetAccountHoldings)로 실측되므로, RuleAlpha가 실제 보유 기준으로 델타만 거래해
재시작이 중복 매수를 일으키지는 않는다(LIVE_KIS.md).
"""

from __future__ import annotations

import subprocess
import threading
import time


class LiveProcessManager:
    def __init__(self, jobs, poll_interval: float = 5.0, base_backoff: float = 5.0,
                 max_backoff: float = 120.0, stable_secs: float = 120.0):
        self._jobs = jobs
        self._lock = threading.Lock()
        self._proc: subprocess.Popen | None = None
        self._job_id: str | None = None

        # 운영자가 원하는 상태(토글). 감독 스레드는 이것만 보고 (재)시작/유지한다.
        self._desired = False
        self._runner = None
        self._build_request = None      # () -> RunRequest (매 재시작 시 최신 config로 새로 빌드)
        self._starting = False          # 잡 제출~Popen 사이(중복 spawn 방지)
        self._started_at: float | None = None  # 현재 프로세스 시작(monotonic) — 백오프 리셋 판정
        self._next_attempt_at = 0.0     # monotonic 게이트(백오프 동안 재시작 보류)
        self._fail_count = 0
        self._last_error: str | None = None

        # 감독 파라미터(테스트는 작게 주입)
        self._poll = float(poll_interval)
        self._base_backoff = float(base_backoff)
        self._max_backoff = float(max_backoff)
        self._stable_secs = float(stable_secs)
        self._supervisor: threading.Thread | None = None
        self._stop_event = threading.Event()

    # ── 상태 ───────────────────────────────────────────────────────────────
    def is_running(self) -> bool:
        with self._lock:
            return self._proc is not None and self._proc.poll() is None

    def status(self) -> dict:
        with self._lock:
            running = self._proc is not None and self._proc.poll() is None
            return {"running": running, "desired": self._desired, "job_id": self._job_id,
                    "fail_count": self._fail_count, "last_error": self._last_error}

    # ── 제어 ───────────────────────────────────────────────────────────────
    def enable(self, runner, build_request) -> str | None:
        """자동매매 ON: desired=ON + 감독 시작 + 즉시 1회 시작 시도. (재)시작 잡 id 반환."""
        with self._lock:
            self._runner = runner
            self._build_request = build_request
            self._desired = True
            self._fail_count = 0
            self._next_attempt_at = 0.0
            self._last_error = None
        self._ensure_supervisor()
        self._tick()  # 토글 즉시성 — 다음 poll까지 안 기다리고 바로 한 번 띄운다
        with self._lock:
            return self._job_id

    def disable(self) -> bool:
        """자동매매 OFF: desired=OFF(재시작 안 함) + 프로세스 종료. 종료했으면 True."""
        with self._lock:
            self._desired = False
        return self._kill_proc()

    def shutdown(self) -> None:
        """서버 종료용 — 감독 중지 + 라이브 프로세스 kill(고아 프로세스 방지).
        config(enabled)는 건드리지 않으므로 다음 부팅 때 재개 대상은 유지된다."""
        self._stop_event.set()
        with self._lock:
            self._desired = False
        self._kill_proc()

    # 하위호환: 기존 start/stop API (테스트·단발 시작용). start는 워치독도 함께 켠다.
    def start(self, runner, request) -> str | None:
        if self.is_running():
            return None
        return self.enable(runner, lambda: request)

    def stop(self) -> bool:
        return self.disable()

    # ── 내부 ───────────────────────────────────────────────────────────────
    def _kill_proc(self) -> bool:
        with self._lock:
            proc = self._proc
            self._proc = None
            self._job_id = None
            self._started_at = None
        if proc is None or proc.poll() is not None:
            return False
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        return True

    def _on_proc(self, proc) -> None:
        # run_live가 Popen 직후 호출 — 핸들 보관 + 시작시각 기록 + starting 해제.
        with self._lock:
            self._proc = proc
            self._started_at = time.monotonic()
            self._starting = False

    def _backoff(self) -> float:
        n = min(self._fail_count, 6)
        return min(self._max_backoff, self._base_backoff * (2 ** max(0, n - 1)))

    def _ensure_supervisor(self) -> None:
        with self._lock:
            if self._supervisor is not None and self._supervisor.is_alive():
                return
            self._stop_event = threading.Event()
            t = threading.Thread(target=self._supervise, name="live-supervisor", daemon=True)
            self._supervisor = t
        t.start()

    def _supervise(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._tick()
            except Exception as e:  # 감독 스레드는 절대 죽지 않는다
                with self._lock:
                    self._last_error = f"supervisor: {type(e).__name__}: {e}"
            self._stop_event.wait(self._poll)

    def _tick(self) -> None:
        """한 번의 감독 사이클: desired=ON인데 안 돌면 (백오프 게이트 통과 시) 재시작."""
        spawn = False
        with self._lock:
            if not self._desired or self._starting:
                return
            running = self._proc is not None and self._proc.poll() is None
            if running:
                return
            now = time.monotonic()
            if self._proc is not None:
                # 직전 프로세스가 종료됨 → 크래시 회계 + 백오프 설정 후 이번 틱은 보류.
                uptime = now - (self._started_at or now)
                code = self._proc.poll()
                if uptime >= self._stable_secs:
                    self._fail_count = 0   # 충분히 오래 살았으면 정상 운영 후 종료 → 백오프 리셋
                else:
                    self._fail_count += 1
                self._proc = None
                self._started_at = None
                wait = self._backoff()
                self._next_attempt_at = now + wait
                self._last_error = f"프로세스 종료(uptime {uptime:.0f}s, exit={code}) — {wait:.0f}s 후 재시작"
                return
            if now < self._next_attempt_at:
                return
            spawn = True
        if spawn:
            self._spawn()

    def _spawn(self) -> str | None:
        with self._lock:
            if self._starting or (self._proc is not None and self._proc.poll() is None):
                return None
            self._starting = True
            runner = self._runner
            build = self._build_request

        def _job(job):
            def on_start(run_id, log_path):
                job.run_id = run_id
                job.log_path = str(log_path)
            try:
                req = build()  # 최신 config로 spec 재구성(유니버스/전략 변경 반영)
                return runner.run_live(req, on_start=on_start, proc_sink=self._on_proc)
            except Exception as e:
                with self._lock:
                    self._last_error = f"라이브 시작/실행 실패: {type(e).__name__}: {e}"
                raise
            finally:
                with self._lock:
                    self._starting = False

        job = self._jobs.submit("라이브 자동매매", _job)
        with self._lock:
            self._job_id = job.id
        return job.id


def build_live_request():
    """현재 config(저장 전략 + 라이브 유니버스)로 라이브 RunRequest를 만든다.

    매 (재)시작 때 호출돼 최신 설정을 반영한다. 전략/유니버스가 없으면 명확한 사유로 예외를 던져
    감독 스레드가 백오프 재시도하거나 라우트가 사용자에게 안내하게 한다.
    """
    import json
    from pathlib import Path

    from . import config
    from .lean.runner import RunRequest

    strategy = config.get_strategy()
    universe = config.get_live_universe()
    if strategy is None:
        raise RuntimeError("전략을 먼저 저장하세요(전략 설정 탭).")
    if not universe:
        raise RuntimeError("대상종목(유니버스)을 먼저 선택하세요.")
    spec = {**strategy, "universe": universe,
            "data_folder": str(Path(config.get_data_folder()).resolve())}
    return RunRequest(
        strategy_path="strategies/RuleStrategy.py",
        data_folder=config.get_data_folder(),
        algorithm_type="RuleStrategy",
        parameters={"rule_spec": json.dumps(spec)},
    )
