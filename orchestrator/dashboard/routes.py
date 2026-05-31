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
import re
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
BACKTEST_CASH = 100_000_000  # 초기자본 1억원 고정(백테스트 폼에서 입력받지 않음)


def _resolve_universe(form, data_folder: str) -> list[str]:
    """유니버스 결정: '적재된 전체 종목' 체크 시 ./data의 가격 적재 종목 전부, 아니면 입력 목록."""
    if form.get("universe_all"):
        from etl.catalog import list_price_tickers
        return list_price_tickers(data_folder)
    return [t.strip() for t in (form.get("universe") or "").split(",") if t.strip()]


def _loaded_count() -> int:
    """적재된 종목 수 — 최초 실행 시 '데이터 먼저 적재' 안내 판단용."""
    from etl.catalog import all_tickers
    return len(all_tickers(config.get_data_folder()))


_PROGRESS_RE = re.compile(r"PROGRESS\s+(\d+)%")


def _parse_progress(lines: list[str]) -> int | None:
    """로그에서 마지막 'PROGRESS NN%'(전략이 시뮬레이션 날짜 기준으로 찍음) 값을 추출."""
    for line in reversed(lines):
        m = _PROGRESS_RE.search(line)
        if m:
            return int(m.group(1))
    return None


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

    # ── 랜딩: 전략 설정 탭으로 ────────────────────────────────────────
    @app.get("/")
    def index_redirect():
        return RedirectResponse(url="/strategy", status_code=307)

    # ── 백테스트 탭 ──────────────────────────────────────────────────
    @app.get("/backtest", response_class=HTMLResponse)
    def backtest_page(request: Request):
        from datetime import date, timedelta
        today = date.today()
        return templates.TemplateResponse(request, "index.html", {
            "runs": store.list_runs(),
            "has_strategy": config.get_strategy() is not None,
            "data_loaded": _loaded_count(),
            "default_data_folder": config.get_data_folder(),
            "start_default": (today - timedelta(days=90)).isoformat(),  # 3개월 전
            "end_default": (today - timedelta(days=1)).isoformat(),     # 오늘 - 1
            "cash": BACKTEST_CASH,
            "error": request.query_params.get("error"),
        })

    @app.post("/backtest")
    async def run_backtest(request: Request):
        # 저장된 단일 전략 + 이 폼의 기간/자본/유니버스로 백테스트 실행.
        from ..rules import parse_rule
        strategy = config.get_strategy()
        if strategy is None:
            return RedirectResponse(url="/backtest?error=먼저 전략 설정에서 전략을 저장하세요", status_code=303)
        try:
            parse_rule(strategy["rule"])  # 저장 시 검증했지만 방어적으로 재확인
        except Exception as e:
            return RedirectResponse(url=f"/backtest?error=전략 규칙식 오류: {e}", status_code=303)

        form = await request.form()
        data_folder = form.get("data_folder") or config.get_data_folder()
        if not data_folder:
            return RedirectResponse(url="/backtest?error=데이터 폴더가 필요합니다(설정)", status_code=303)
        spec = {
            **strategy,  # signals, rule, period_days
            "universe": _resolve_universe(form, data_folder),
            "start": form.get("start"), "end": form.get("end"),
            "cash": BACKTEST_CASH,  # 초기자본은 1억으로 고정(입력받지 않음)
        }
        if not spec["universe"]:
            return RedirectResponse(url="/backtest?error=유니버스(종목)를 지정하세요", status_code=303)
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
        groups = strategy.get("groups") or signals_catalog.DEFAULT_GROUPS
        return templates.TemplateResponse(request, "strategy.html", {
            "catalog": signals_catalog.CATALOG,
            "strategy": strategy,
            "groups": groups,
            "param_value": signals_catalog.param_value,
            "risk": config.risk_form_values(),
            "data_loaded": _loaded_count(),
            "saved": request.query_params.get("saved"),
            "error": request.query_params.get("error"),
        })

    @app.post("/strategy")
    async def strategy_save(request: Request):
        from .. import signals_catalog
        from ..rules import parse_rule
        form = await request.form()
        # 사용자 친화 빌더: 조건 그룹(체크박스)에서 규칙식을 생성(그룹 안 AND, 그룹끼리 OR).
        groups = signals_catalog.groups_from_form(form)
        if not groups:
            return RedirectResponse(url="/strategy?error=조건을 하나 이상 선택하세요", status_code=303)
        rule = signals_catalog.rule_from_groups(groups)
        try:
            parse_rule(rule)  # 생성식 방어적 검증
        except Exception as e:
            return RedirectResponse(url=f"/strategy?error=규칙식 오류: {e}", status_code=303)
        spec = {
            "signals": signals_catalog.signals_from_form(form),
            "rule": rule,
            "groups": groups,
            "period_days": int(form.get("period_days") or signals_catalog.DEFAULT_PERIOD_DAYS),
        }
        config.save_strategy(spec)
        # 리스크 설정도 같은 화면에서 저장
        config.save_risk({k: form.get(k, "") for k in config.RISK_KEYS})
        return RedirectResponse(url="/strategy?saved=1", status_code=303)

    # ── ③ 설정 탭 (키 + 데이터 적재) ─────────────────────────────────
    @app.get("/data", response_class=HTMLResponse)
    def data_list(request: Request):
        # 목록은 '파일 존재 여부'만 본다(glob) — 수천 종목의 일봉/수급을 다 파싱하면
        # 페이지가 멈추므로, 상세 행 수는 종목 상세(/data/{ticker})에서만 계산한다.
        from etl import catalog
        data_dir = config.get_data_folder()
        price = set(catalog.list_price_tickers(data_dir))
        flow = set(catalog.list_flow_tickers(data_dir))
        tickers = [{"ticker": t, "price": t in price, "flow": t in flow}
                   for t in sorted(price | flow)]
        return templates.TemplateResponse(request, "data_list.html", {
            "tickers": tickers, "count": len(tickers), "data_dir": data_dir,
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
        # 오래 걸려 진행이 궁금하므로 진행 로그를 파일로 남기고 job.log_path로 실시간 표시.
        from datetime import datetime
        from etl.universe import ingest_all_market
        data_dir = config.get_data_folder()

        def _job(job):
            log_path = REPO_ROOT / "runs" / f"loadall-{job.id}.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            job.log_path = str(log_path)
            with open(log_path, "a", encoding="utf-8", buffering=1) as f:
                def on_progress(msg):
                    f.write(f"{datetime.now():%H:%M:%S} {msg}\n")
                info = ingest_all_market(data_dir, on_progress=on_progress)
            return f"OHLCV {info['price_tickers']}종목 · 수급 {info['flow_ok']}종목"

        jobs.submit("전체시장 적재(OHLCV+수급)", _job)
        return RedirectResponse(url="/jobs", status_code=303)

    @app.get("/jobs", response_class=HTMLResponse)
    def jobs_page(request: Request):
        return templates.TemplateResponse(request, "jobs.html", {"jobs": jobs.list()})

    @app.get("/jobs/{job_id}", response_class=HTMLResponse)
    def job_detail(request: Request, job_id: str):
        job = jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")
        log_tail, progress = "", None
        if job.log_path and Path(job.log_path).exists():
            lines = Path(job.log_path).read_text(encoding="utf-8", errors="replace").splitlines()
            log_tail = "\n".join(lines[-60:])  # 최근 60줄
            progress = _parse_progress(lines)  # 백테스트 진행률(%) — 'PROGRESS NN%' 마지막 값
        return templates.TemplateResponse(
            request, "job_detail.html", {"job": job, "log_tail": log_tail, "progress": progress})

    @app.get("/settings", response_class=HTMLResponse)
    def settings_page(request: Request):
        return templates.TemplateResponse(request, "settings.html", {
            "secrets": config.secret_status(),
            "data_dir": config.get_data_folder(),
            "data_loaded": _loaded_count(),
            "saved": request.query_params.get("saved"),
            "error": request.query_params.get("error"),
        })

    @app.post("/settings")
    async def settings_save(request: Request):
        # 폼은 시크릿 키별 입력(동적). save_secrets가 유효 키/빈값을 필터.
        form = await request.form()
        config.save_secrets({k: str(v) for k, v in form.items()})
        return RedirectResponse(url="/settings?saved=1", status_code=303)
