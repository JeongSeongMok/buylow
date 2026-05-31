"""대시보드 HTML 라우트 (HTMX).

3개 챕터로 구성:
  ① 전략 설정(/strategy) — 조건식 + 리스크를 저장(단일 전략).
  ② 백테스트(/)         — 저장된 전략을 기간/자본/유니버스만 정해 실행 + 결과 조회.
  ③ 설정(/settings)     — API 키 + 과거 데이터 일괄 적재.

API 앱에 register_dashboard(app, ...)로 얹는다. 의존성(runner getter, store, 실행 헬퍼)은
주입받아 테스트 가능하게 한다.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from fastapi import FastAPI, HTTPException, Request
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
    run_and_store: Callable[..., dict[str, Any]],
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

    # ── ② 백테스트 탭 ────────────────────────────────────────────────
    @app.get("/", response_class=HTMLResponse)
    def index(request: Request):
        return templates.TemplateResponse(request, "index.html", {
            "runs": store.list_runs(),
            "has_strategy": config.get_strategy() is not None,
            "default_data_folder": config.get_data_folder(),
            "error": request.query_params.get("error"),
        })

    @app.post("/backtest")
    async def run_backtest(request: Request):
        # 저장된 단일 전략 + 이 폼의 기간/자본/유니버스로 백테스트 실행.
        from ..rules import parse_rule
        strategy = config.get_strategy()
        if strategy is None:
            return RedirectResponse(url="/?error=먼저 ① 전략 설정에서 전략을 저장하세요", status_code=303)
        try:
            parse_rule(strategy["rule"])  # 저장 시 검증했지만 방어적으로 재확인
        except Exception as e:
            return RedirectResponse(url=f"/?error=전략 규칙식 오류: {e}", status_code=303)

        form = await request.form()
        data_folder = form.get("data_folder") or config.get_data_folder()
        if not data_folder:
            return RedirectResponse(url="/?error=데이터 폴더가 필요합니다(③ 설정)", status_code=303)
        spec = {
            **strategy,  # signals, rule, period_days
            "universe": _resolve_universe(form, data_folder),
            "start": form.get("start"), "end": form.get("end"),
            "cash": int(form.get("cash") or 10_000_000),
        }
        if not spec["universe"]:
            return RedirectResponse(url="/?error=유니버스(종목)를 지정하세요", status_code=303)
        req = RunRequest(
            strategy_path="strategies/RuleStrategy.py",
            data_folder=data_folder,
            algorithm_type="RuleStrategy",
            parameters={"rule_spec": json.dumps(spec)},
        )
        job = submit_backtest("백테스트", req)
        return RedirectResponse(url=f"/jobs/{job.id}", status_code=303)

    @app.get("/ui/runs/{run_id}", response_class=HTMLResponse)
    def ui_run_detail(request: Request, run_id: str):
        record = store.get_run(run_id)
        if record is None:
            raise HTTPException(status_code=404, detail=f"run not found: {run_id}")
        return templates.TemplateResponse(request, "run_detail.html", {"run": record})

    # ── ① 전략 설정 탭 ───────────────────────────────────────────────
    @app.get("/strategy", response_class=HTMLResponse)
    def strategy_page(request: Request):
        from .. import signals_catalog
        strategy = config.get_strategy() or signals_catalog.default_strategy()
        return templates.TemplateResponse(request, "strategy.html", {
            "catalog": signals_catalog.CATALOG,
            "strategy": strategy,
            "param_value": signals_catalog.param_value,
            "risk": config.get_risk_config(),
            "saved": request.query_params.get("saved"),
            "error": request.query_params.get("error"),
        })

    @app.post("/strategy")
    async def strategy_save(request: Request):
        from .. import signals_catalog
        from ..rules import parse_rule
        form = await request.form()
        rule = (form.get("rule") or "").strip()
        try:
            parse_rule(rule)
        except Exception as e:
            return RedirectResponse(url=f"/strategy?error=규칙식 오류: {e}", status_code=303)
        spec = {
            "signals": signals_catalog.signals_from_form(form),
            "rule": rule,
            "period_days": int(form.get("period_days") or signals_catalog.DEFAULT_PERIOD_DAYS),
        }
        config.save_strategy(spec)
        # 리스크 설정도 같은 화면에서 저장
        config.save_risk({k: form.get(k, "") for k in config.RISK_KEYS})
        return RedirectResponse(url="/strategy?saved=1", status_code=303)

    # ── ③ 설정 탭 (키 + 데이터 적재) ─────────────────────────────────
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

    @app.post("/data/load-all")
    def load_all_market(request: Request):
        # 버튼 하나로 한국시장 전체(OHLCV+수급) 일괄 적재(덮어쓰기). 무거우니 백그라운드 잡.
        from etl.universe import ingest_all_market
        data_dir = config.get_data_folder()
        jobs.submit("전체시장 적재(OHLCV+수급)", lambda job: ingest_all_market(data_dir))
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
            "data_dir": config.get_data_folder(),
            "saved": request.query_params.get("saved"),
            "error": request.query_params.get("error"),
        })

    @app.post("/settings")
    async def settings_save(request: Request):
        # 폼은 시크릿 키별 입력(동적). save_secrets가 유효 키/빈값을 필터.
        form = await request.form()
        config.save_secrets({k: str(v) for k, v in form.items()})
        return RedirectResponse(url="/settings?saved=1", status_code=303)
