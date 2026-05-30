"""대시보드 HTML 라우트 (HTMX).

API 앱에 register_dashboard(app, ...)로 얹는다. 의존성(runner getter, store, 실행 헬퍼)은
주입받아 테스트 가능하게 한다.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from ..lean import RunRequest
from ..lean.environment import REPO_ROOT

_HERE = Path(__file__).resolve().parent
TEMPLATES_DIR = _HERE / "templates"
STATIC_DIR = _HERE / "static"
STRATEGIES_DIR = REPO_ROOT / "strategies"


def available_strategies() -> list[dict[str, str]]:
    """strategies/ 의 전략 .py 목록 (전략 레지스트리의 최소 버전)."""
    if not STRATEGIES_DIR.is_dir():
        return []
    return [
        {"path": f"strategies/{p.name}", "name": p.stem}
        for p in sorted(STRATEGIES_DIR.glob("*.py"))
    ]


def register_dashboard(
    app: FastAPI,
    *,
    get_runner: Callable[[], Any],
    store: Any,
    run_and_store: Callable[[Any, Any, RunRequest], dict[str, Any]],
) -> None:
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request):
        return templates.TemplateResponse(request, "index.html", {
            "strategies": available_strategies(),
            "runs": store.list_runs(),
            "default_data_folder": os.environ.get("LEAN_DATA_DIR", ""),
        })

    @app.post("/ui/runs", response_class=HTMLResponse)
    def ui_create_run(
        request: Request,
        strategy: str = Form(...),
        data_folder: str = Form(""),
        algorithm_type: str = Form(""),
    ):
        df = data_folder or os.environ.get("LEAN_DATA_DIR", "")
        if not df:
            # 데이터 폴더 없이는 실행 불가 — 폼 위치에 에러 partial 반환
            return templates.TemplateResponse(
                request, "partials/runs_table.html",
                {"runs": store.list_runs(), "error": "데이터 폴더(LEAN_DATA_DIR)가 필요합니다."},
            )
        req = RunRequest(
            strategy_path=strategy,
            data_folder=df,
            algorithm_type=(algorithm_type or None),
        )
        run_and_store(get_runner(), store, req)  # 동기 실행(스레드풀) → 저장
        return templates.TemplateResponse(
            request, "partials/runs_table.html", {"runs": store.list_runs()},
        )

    @app.get("/ui/runs/{run_id}", response_class=HTMLResponse)
    def ui_run_detail(request: Request, run_id: str):
        record = store.get_run(run_id)
        if record is None:
            raise HTTPException(status_code=404, detail=f"run not found: {run_id}")
        return templates.TemplateResponse(request, "run_detail.html", {"run": record})
