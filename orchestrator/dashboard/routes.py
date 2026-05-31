"""대시보드 HTML 라우트 (HTMX).

API 앱에 register_dashboard(app, ...)로 얹는다. 의존성(runner getter, store, 실행 헬퍼)은
주입받아 테스트 가능하게 한다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .. import config
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
    jobs: Any,
) -> None:
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request):
        return templates.TemplateResponse(request, "index.html", {
            "strategies": available_strategies(),
            "runs": store.list_runs(),
            "default_data_folder": config.get_data_folder(),
            "missing_secrets": [s.label for s in config.missing_secrets()],
        })

    @app.post("/ui/runs", response_class=HTMLResponse)
    def ui_create_run(
        request: Request,
        strategy: str = Form(...),
        data_folder: str = Form(""),
        algorithm_type: str = Form(""),
    ):
        df = data_folder or config.get_data_folder()
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

    @app.get("/data", response_class=HTMLResponse)
    def data_list(request: Request):
        from etl import catalog
        data_dir = config.get_data_folder()
        tickers = [
            {"ticker": t,
             "price": len(catalog.read_price_daily(data_dir, t)),
             "flow": len(catalog.read_flow(data_dir, t))}
            for t in catalog.all_tickers(data_dir)
        ]
        return templates.TemplateResponse(request, "data_list.html", {
            "tickers": tickers, "data_dir": data_dir,
            "error": request.query_params.get("error"),
        })

    @app.get("/data/{ticker}", response_class=HTMLResponse)
    def data_detail(request: Request, ticker: str):
        from etl import catalog
        summary = catalog.ticker_summary(config.get_data_folder(), ticker)
        return templates.TemplateResponse(request, "data_detail.html", {"d": summary})

    @app.post("/data/fetch")
    async def data_fetch(request: Request):
        from datetime import date
        form = await request.form()
        ticker = (form.get("ticker") or "").strip()
        kinds = form.getlist("kind")
        data_dir = config.get_data_folder()
        try:
            start = date.fromisoformat(form["from"])
            end = date.fromisoformat(form["to"]) if form.get("to") else date.today()
            if not ticker:
                raise ValueError("종목코드를 입력하세요")
            if "price" in kinds:
                from etl import krx as etl_krx
                etl_krx.ingest(ticker, start, end, data_dir)
            if "flow" in kinds:
                from etl import flow as etl_flow
                etl_flow.ingest_flow(ticker, start, end, data_dir)
        except Exception as e:  # 네트워크/로그인/입력 오류를 사용자에게 표시
            return RedirectResponse(url=f"/data?error={type(e).__name__}: {e}", status_code=303)
        return RedirectResponse(url=f"/data/{ticker}", status_code=303)

    @app.post("/data/universe")
    def load_universe(request: Request, market: str = Form("KOSPI200"), years: int = Form(3)):
        # 대량/장기 적재는 요청을 막지 않도록 백그라운드 잡으로 던진다.
        from datetime import date, timedelta
        from etl.universe import ingest_universe
        data_dir = config.get_data_folder()
        end = date.today()
        start = end - timedelta(days=365 * years)
        jobs.submit(
            f"유니버스 적재 {market} {years}년",
            lambda: ingest_universe(start, end, market, data_dir),
        )
        return RedirectResponse(url="/jobs", status_code=303)

    @app.get("/jobs", response_class=HTMLResponse)
    def jobs_page(request: Request):
        return templates.TemplateResponse(request, "jobs.html", {"jobs": jobs.list()})

    @app.get("/settings", response_class=HTMLResponse)
    def settings_page(request: Request):
        return templates.TemplateResponse(request, "settings.html", {
            "secrets": config.secret_status(),
            "saved": request.query_params.get("saved"),
        })

    @app.post("/settings")
    async def settings_save(request: Request):
        # 폼은 시크릿 키별 입력(동적). save_secrets가 유효 키/빈값을 필터.
        form = await request.form()
        config.save_secrets({k: str(v) for k, v in form.items()})
        return RedirectResponse(url="/settings?saved=1", status_code=303)
