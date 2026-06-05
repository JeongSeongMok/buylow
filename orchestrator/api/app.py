"""Control API 골격.

대시보드(브라우저)와 CLI가 공유하는 단일 계약(docs/ARCHITECTURE.md). 지금은 백테스트 실행/조회만.
전략 레지스트리·라이브 등은 같은 패턴으로 이후 확장한다.

설계: create_app(runner, store)로 의존성을 주입받아 테스트에서 가짜 runner/임시 DB를 끼울 수 있게 한다.
기본 runner(LeanRunner)는 생성 시 런처 빌드 등 비용이 크므로, 첫 실행 요청 때 lazy하게 만든다.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from ..config import get_data_folder
from ..dashboard import register_dashboard
from ..jobs import JobManager
from ..lean import LeanRunner, RunRequest, RunResult
from ..persistence import RunStore, TradeStore


class RunCreate(BaseModel):
    """백테스트 실행 요청 본문."""

    strategy: str = "strategies/SmokeTestAlgorithm.py"
    data_folder: str | None = None            # 없으면 LEAN_DATA_DIR 환경변수 사용
    algorithm_type: str | None = None
    parameters: dict[str, str] = Field(default_factory=dict)


def _result_to_record(req: RunRequest, result: RunResult) -> dict[str, Any]:
    """RunRequest+RunResult → 영속화 record dict (lean 타입을 persistence와 분리)."""
    return {
        "run_id": result.run_id,
        "strategy": req.strategy_path,
        "algorithm_type": req.resolved_algorithm_type(),
        "data_folder": req.data_folder,
        "parameters": req.parameters,
        "exit_code": result.exit_code,
        "success": result.success,
        "statistics": result.statistics,
        "run_dir": str(result.run_dir),
        "log_path": str(result.log_path),
        "result_json": str(result.result_json) if result.result_json else None,
    }


def run_and_store(runner: LeanRunner, store: RunStore, req: RunRequest, on_start=None) -> dict[str, Any]:
    """백테스트 실행 → 결과 저장. JSON API와 대시보드가 공유하는 단일 경로.
    on_start(run_id, log_path)는 백그라운드 잡의 진행 추적용으로 전달된다."""
    result = runner.run_backtest(req, on_start=on_start)
    return store.save_run(_result_to_record(req, result))


def _default_get_broker():
    """기본 매매 조회 브로커(활성 증권사). 테스트는 create_app(get_broker=...)로 가짜를 주입."""
    from brokers.kis_broker import get_trading_broker
    return get_trading_broker()


def create_app(runner: LeanRunner | None = None, store: RunStore | None = None,
               jobs: JobManager | None = None, trade_store: TradeStore | None = None,
               get_broker=None, broker_cache=None, live_manager=None) -> FastAPI:
    # 활성 증권사 기준 잔고·당일 체결을 백그라운드로 주기 갱신해 메모리 캐시(매매 탭 즉시 표시).
    _get_broker = get_broker or _default_get_broker
    if broker_cache is None:
        from ..broker_cache import BrokerCache
        broker_cache = BrokerCache(_get_broker)

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _lifespan(_app):
        broker_cache.start()  # 서버 가동 동안 백그라운드 캐시 갱신
        try:
            yield
        finally:
            broker_cache.stop()

    app = FastAPI(title="buylow", version="0.0.1", lifespan=_lifespan)
    # runner는 lazy: 주입되지 않았으면 첫 실행 때 생성(런처 빌드 비용을 startup에서 회피)
    state: dict[str, Any] = {"runner": runner, "store": store or RunStore(),
                             "jobs": jobs or JobManager(),
                             "trade_store": trade_store or TradeStore()}

    def get_runner() -> LeanRunner:
        if state["runner"] is None:
            state["runner"] = LeanRunner()
        return state["runner"]

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/runs")
    def create_run(payload: RunCreate) -> dict[str, Any]:
        # 동기(blocking) 실행 — FastAPI가 sync 핸들러를 스레드풀에서 돌려 이벤트루프를 막지 않음.
        # (장기 백테스트의 비동기/백그라운드 큐 전환은 이후 단계)
        data_folder = payload.data_folder or get_data_folder()
        if not data_folder:
            raise HTTPException(status_code=400, detail="data_folder 또는 LEAN_DATA_DIR 필요")
        req = RunRequest(
            strategy_path=payload.strategy,
            data_folder=data_folder,
            algorithm_type=payload.algorithm_type,
            parameters=payload.parameters,
        )
        return run_and_store(get_runner(), state["store"], req)

    @app.get("/runs")
    def list_runs() -> list[dict[str, Any]]:
        return state["store"].list_runs()

    @app.get("/runs/{run_id}")
    def get_run(run_id: str) -> dict[str, Any]:
        record = state["store"].get_run(run_id)
        if record is None:
            raise HTTPException(status_code=404, detail=f"run not found: {run_id}")
        return record

    # 라이브 자동매매 프로세스 매니저(매매 탭 토글로 LEAN 라이브 start/stop, 킬 스위치)
    if live_manager is None:
        from ..live_runner import LiveProcessManager
        live_manager = LiveProcessManager(state["jobs"])

    # 브라우저 대시보드(HTML) 라우트를 같은 앱에 얹는다
    register_dashboard(
        app,
        get_runner=get_runner,
        store=state["store"],
        run_and_store=run_and_store,
        jobs=state["jobs"],
        trade_store=state["trade_store"],
        get_broker=_get_broker,  # 테스트는 가짜 브로커 주입; 없으면 기본 KIS 브로커
        broker_cache=broker_cache,
        live_manager=live_manager,
    )

    return app
