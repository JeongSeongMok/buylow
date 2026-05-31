"""대시보드 HTML 라우트 (HTMX).

API 앱에 register_dashboard(app, ...)로 얹는다. 의존성(runner getter, store, 실행 헬퍼)은
주입받아 테스트 가능하게 한다.
"""

from __future__ import annotations

import json
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


def _resolve_universe(form, data_folder: str) -> list[str]:
    """유니버스 결정: '적재된 전체 종목' 체크 시 ./data의 가격 적재 종목 전부, 아니면 입력 목록."""
    if form.get("universe_all"):
        from etl.catalog import list_price_tickers
        return list_price_tickers(data_folder)
    return [t.strip() for t in (form.get("universe") or "").split(",") if t.strip()]


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

    def submit_backtest(name: str, req: RunRequest):
        """백테스트를 백그라운드 잡으로 실행(요청 비차단). 잡에 run_id/log_path를 실어 진행 추적."""
        def _bt(job):
            def on_start(run_id, log_path):
                job.run_id = run_id
                job.log_path = str(log_path)
            rec = run_and_store(get_runner(), store, req, on_start=on_start)
            return f"{rec['run_id']} · 주문 {rec['statistics'].get('Total Orders','-')} · Net {rec['statistics'].get('Net Profit','-')}"
        return jobs.submit(name, _bt)

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request):
        # ② 백테스트 챕터 — 결과/이력. 새 실행은 ① 전략 설정에서.
        return templates.TemplateResponse(request, "index.html", {"runs": store.list_runs()})

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

    @app.get("/compose", response_class=HTMLResponse)
    def compose_page(request: Request):
        from .. import strategy_catalog
        return templates.TemplateResponse(request, "compose.html", {
            "catalog": strategy_catalog.CATALOG,
            "default_data_folder": config.get_data_folder(),
        })

    @app.post("/compose")
    async def compose_run(request: Request):
        from .. import strategy_catalog
        form = await request.form()
        selected = form.getlist("alpha")
        alphas = []
        for spec in strategy_catalog.CATALOG:
            if spec.name in selected:
                raw = {p.key: form.get(f"{spec.name}__{p.key}", "") for p in spec.params}
                alphas.append({"name": spec.name, "params": strategy_catalog.cast_params(spec.name, raw)})
        if not alphas:
            return RedirectResponse(url="/compose", status_code=303)

        data_folder = form.get("data_folder") or config.get_data_folder()
        composition = {
            "alphas": alphas,
            "universe": _resolve_universe(form, data_folder),
            "start": form.get("start"), "end": form.get("end"),
            "cash": int(form.get("cash") or 10_000_000),
        }
        req = RunRequest(
            strategy_path="strategies/Composed.py",
            data_folder=data_folder,
            algorithm_type="Composed",
            parameters={"composition": json.dumps(composition)},
        )
        job = submit_backtest("백테스트 · Alpha조합", req)
        return RedirectResponse(url=f"/jobs/{job.id}", status_code=303)

    @app.get("/rules", response_class=HTMLResponse)
    def rules_page(request: Request):
        from .. import signals_catalog
        return templates.TemplateResponse(request, "rules.html", {
            "catalog": signals_catalog.CATALOG,
            "default_data_folder": config.get_data_folder(),
            "error": request.query_params.get("error"),
        })

    @app.post("/rules")
    async def rules_run(request: Request):
        from .. import signals_catalog
        from ..rules import parse_rule
        form = await request.form()
        rule = (form.get("rule") or "").strip()
        # 모든 signal을 그 파라미터로 구성(식에 쓰인 것만 RuleAlpha가 평가)
        signals = {
            spec.label: {"type": spec.type, "params": signals_catalog.cast_params(spec.label, form)}
            for spec in signals_catalog.CATALOG
        }
        try:
            parse_rule(rule)  # 식 검증
        except Exception as e:
            return RedirectResponse(url=f"/rules?error=규칙식 오류: {e}", status_code=303)

        data_folder = form.get("data_folder") or config.get_data_folder()
        spec = {
            "signals": signals, "rule": rule,
            "universe": _resolve_universe(form, data_folder),
            "start": form.get("start"), "end": form.get("end"),
            "cash": int(form.get("cash") or 10_000_000),
            "period_days": int(form.get("period_days") or 5),
        }
        req = RunRequest(
            strategy_path="strategies/RuleStrategy.py",
            data_folder=data_folder,
            algorithm_type="RuleStrategy",
            parameters={"rule_spec": json.dumps(spec)},
        )
        job = submit_backtest("백테스트 · 규칙전략", req)
        return RedirectResponse(url=f"/jobs/{job.id}", status_code=303)

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
            if "fundamental" in kinds:
                from etl import fundamental as etl_fund
                etl_fund.ingest_fundamental(ticker, start, end, data_dir)
        except Exception as e:  # 네트워크/로그인/입력 오류를 설정 화면에 표시(적재 폼이 설정에 있음)
            return RedirectResponse(url=f"/settings?error={type(e).__name__}: {e}", status_code=303)
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
            lambda job: ingest_universe(start, end, market, data_dir),
        )
        return RedirectResponse(url="/jobs", status_code=303)

    @app.get("/jobs", response_class=HTMLResponse)
    def jobs_page(request: Request):
        return templates.TemplateResponse(request, "jobs.html", {"jobs": jobs.list()})

    @app.get("/jobs/{job_id}", response_class=HTMLResponse)
    def job_detail(request: Request, job_id: str):
        job = jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")
        log_tail = ""
        if job.log_path and Path(job.log_path).exists():
            lines = Path(job.log_path).read_text(encoding="utf-8", errors="replace").splitlines()
            log_tail = "\n".join(lines[-60:])  # 최근 60줄
        return templates.TemplateResponse(request, "job_detail.html", {"job": job, "log_tail": log_tail})

    @app.get("/settings", response_class=HTMLResponse)
    def settings_page(request: Request):
        return templates.TemplateResponse(request, "settings.html", {
            "secrets": config.secret_status(),
            "risk": config.get_risk_config(),
            "saved": request.query_params.get("saved"),
            "error": request.query_params.get("error"),
        })

    @app.post("/settings/risk")
    async def settings_risk_save(request: Request):
        form = await request.form()
        config.save_risk({k: form.get(k, "") for k in config.RISK_KEYS})
        return RedirectResponse(url="/settings?saved=1", status_code=303)

    @app.post("/settings")
    async def settings_save(request: Request):
        # 폼은 시크릿 키별 입력(동적). save_secrets가 유효 키/빈값을 필터.
        form = await request.form()
        config.save_secrets({k: str(v) for k, v in form.items()})
        return RedirectResponse(url="/settings?saved=1", status_code=303)
