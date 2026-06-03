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
# 동시 보유 종목 상한. 1억을 이 수로 나눠도 종목당 배분이 1주 이상 되도록(=균등분할 가능) 한다.
# 전체 종목 스캔은 유지하되, 매수 신호가 이보다 많으면 유동성 상위만 보유.
MAX_POSITIONS = 20


def _resolve_universe(form, data_folder: str) -> list[str]:
    """유니버스 결정: '전체 종목 대상' 시 적재된 전 종목을 스캔, 아니면 입력한 종목 목록.

    유니버스(스캔 대상)는 줄이지 않는다. 매수 신호가 자본 대비 너무 많아 균등분할이 안 되는 문제는
    포트폴리오 단계(전략의 max_positions)에서 보유 종목 수를 제한해 해결한다.
    """
    if form.get("universe_all"):
        from etl.catalog import list_price_tickers
        return list_price_tickers(data_folder)
    return [t.strip() for t in (form.get("universe") or "").split(",") if t.strip()]


def _resolve_minute_tickers(form) -> tuple[list[str], list[str]]:
    """분봉 적재 대상 = 직접 입력(universe csv) + 인덱스(KOSPI200/KOSDAQ150) 구성종목. 중복 제거.

    적재가 목적이라 '이미 로드된 것'과 교집합하지 않는다(새 분봉을 받는 것이므로).
    반환: (티커목록, 에러메시지목록). 인덱스 조회 실패/공집합은 에러로 모아 라우트가 사용자에게 알린다.
    """
    from etl.universe import INDEX_CODES, list_universe
    out: list[str] = []
    seen: set[str] = set()
    errors: list[str] = []

    for t in (form.get("universe") or "").split(","):
        t = t.strip()
        if t and t not in seen:
            seen.add(t); out.append(t)

    indices = form.getlist("index") if hasattr(form, "getlist") else [form.get("index")]
    if any(k and k.upper() in INDEX_CODES for k in indices):
        # 휴장일이면 지수 구성종목(deposit file)이 비므로 가장 가까운 영업일로 조회.
        on = None
        try:
            from pykrx import stock
            from datetime import datetime
            on = datetime.strptime(stock.get_nearest_business_day_in_a_week(), "%Y%m%d").date()
        except Exception:
            pass
        for key in indices:
            if not key or key.upper() not in INDEX_CODES:
                continue
            try:
                members = list_universe(key.upper(), on)
            except Exception as e:
                errors.append(f"{key} 구성종목 조회 실패({type(e).__name__}) — 설정 탭에서 KRX 로그인이 필요할 수 있습니다")
                continue
            if not members:
                errors.append(f"{key} 구성종목이 비어 있습니다(날짜/권한 확인)")
                continue
            for t in members:
                if t not in seen:
                    seen.add(t); out.append(t)
    return out, errors


def _loaded_count() -> int:
    """적재된 종목 수 — 최초 실행 시 '데이터 먼저 적재' 안내 판단용."""
    from etl.catalog import all_tickers
    return len(all_tickers(config.get_data_folder()))


def format_won(amount) -> str:
    """금액을 한국어(억/만원)로. 예: 147000257 → '1억 4,700만원', 2339943 → '234만원'."""
    try:
        n = int(round(float(amount)))
    except (TypeError, ValueError):
        return str(amount)
    sign = "-" if n < 0 else ""
    n = abs(n)
    if n < 10_000:
        return f"{sign}{n:,}원"
    eok, rem = divmod(n, 100_000_000)
    man = round(rem / 10_000)
    parts = []
    if eok:
        parts.append(f"{eok}억")
    if man:
        parts.append(f"{man:,}만")
    return f"{sign}{' '.join(parts) or '0'}원"


def _num(stats: dict, key: str):
    """통계 문자열에서 숫자만 추출(%, KRW, $, 콤마 제거). 실패 시 None."""
    v = stats.get(key)
    if v is None:
        return None
    s = str(v).replace("KRW", "").replace("$", "").replace("%", "").replace(",", "").strip()
    try:
        return float(s)
    except ValueError:
        return None


def _pct(stats: dict, key: str):
    """퍼센트 통계를 보기 좋게(소수 1자리, 불필요한 0 제거). 예: '119.311%' → '119.3%'."""
    n = _num(stats, key)
    if n is None:
        return None
    return f"{n:.1f}".rstrip("0").rstrip(".") + "%"


def parse_rule_reasons(run_dir) -> dict:
    """RuleAlpha가 남긴 'RULEHIT 날짜 종목 BUY|SELL 시그널들' 로그를 파싱.

    반환: {(YYYY-MM-DD, 종목, 'BUY'|'SELL'): '발동 시그널들'} — 거래 내역의 '사유'에 병합.
    """
    reasons: dict = {}
    if not run_dir or not Path(run_dir).is_dir():
        return reasons
    for f in Path(run_dir).glob("*.txt"):
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        for m in re.finditer(r"RULEHIT (\d{4}-\d{2}-\d{2}) (\S+) (BUY|SELL) ?(.*)", text):
            reasons[(m.group(1), m.group(2), m.group(3))] = m.group(4).strip()
    return reasons


def parse_orders(result_json, reasons=None, names=None) -> list[dict]:
    """LEAN 결과 JSON에서 체결 주문 내역을 사람이 보기 좋게 추출(거래 히스토리용).

    '사유'는 리스크 태그(손절/익절) > RuleAlpha 로그의 트리거 시그널 > 일반 라벨 순으로 채운다.
    names(코드→이름)가 있으면 종목명도 채운다.
    """
    reasons = reasons or {}
    names = names or {}
    if not result_json:
        return []
    p = Path(result_json)
    # 저장된 경로는 보통 '-summary.json'(주문 미포함 요약본) → 주문이 든 전체 결과 파일로 교체
    if p.name.endswith("-summary.json"):
        p = p.with_name(p.name.replace("-summary.json", ".json"))
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return []
    orders = data.get("orders") or {}
    rows = []
    for o in (orders.values() if isinstance(orders, dict) else orders):
        if o.get("status") != 3:  # 3 = Filled (체결된 것만)
            continue
        qty = o.get("quantity", 0) or 0
        tag = (o.get("tag") or "").strip()
        buy = qty > 0
        time10 = (o.get("lastFillTime") or o.get("time") or "")[:10]
        ticker = (o.get("symbol") or {}).get("value", "-")
        hit = reasons.get((time10, ticker, "BUY" if buy else "SELL"))
        rows.append({
            "time": time10,
            "ticker": ticker,
            "name": names.get(ticker, ""),
            "side": "매수" if buy else "매도",
            "buy": buy,
            "qty": abs(int(qty)),
            "price": f"{o.get('price', 0):,.0f}",
            "amount": format_won(abs(o.get("value", 0))),
            "reason": tag or hit or ("진입(전략 신호)" if buy else "청산(신호 변화/리스크)"),
        })
    rows.sort(key=lambda r: r["time"])
    return rows


def friendly_stats(stats: dict) -> list[dict]:
    """LEAN 통계(영문·원시)를 사용자용 한국어 핵심 지표로 변환. 이해 어려운 항목은 생략."""
    rows: list[dict] = []

    def add(label, value, note=""):
        if value is not None and value != "":
            rows.append({"label": label, "value": value, "note": note})

    start, end = _num(stats, "Start Equity"), _num(stats, "End Equity")
    add("총 수익률", _pct(stats, "Net Profit"), "백테스트 기간 전체 수익률")
    if start is not None and end is not None:
        diff = end - start
        add("순손익", ("+" if diff >= 0 else "-") + format_won(abs(diff)))
        add("최종 자산", format_won(end))
        add("시작 자본", format_won(start))
    add("연환산 수익률", _pct(stats, "Compounding Annual Return"),
        "1년 기준 환산값(기간이 짧으면 과장될 수 있음)")
    add("최대 낙폭(MDD)", _pct(stats, "Drawdown"), "고점 대비 최대 하락폭")
    add("총 거래 횟수", str(int(_num(stats, "Total Orders"))) + "회"
        if _num(stats, "Total Orders") is not None else None)
    add("승률", _pct(stats, "Win Rate"))
    aw, al = _pct(stats, "Average Win"), _pct(stats, "Average Loss")
    if aw and al:
        add("평균 수익 / 손실", f"{aw} / {al}", "이긴 거래 / 진 거래의 평균")
    plr = _num(stats, "Profit-Loss Ratio")
    if plr is not None:
        add("손익비", f"{plr:.2f}", "평균수익 ÷ 평균손실 (1보다 크면 유리)")
    shp = _num(stats, "Sharpe Ratio")
    if shp is not None:
        add("샤프 지수", f"{shp:.2f}", "위험 대비 수익 (대략 1↑ 양호, 2↑ 우수)")
    fees = _num(stats, "Total Fees")
    if fees is not None:
        add("총 수수료", format_won(fees))
    return rows


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
        from etl.names import load_names
        today = date.today()
        return templates.TemplateResponse(request, "index.html", {
            "runs": store.list_runs(),
            "has_strategy": config.get_strategy() is not None,
            "data_loaded": _loaded_count(),
            "default_data_folder": config.get_data_folder(),
            "names": load_names(config.get_data_folder()),  # 종목명 검색·칩 표시용(클라이언트 임베드)
            "start_default": (today - timedelta(days=90)).isoformat(),  # 3개월 전
            "end_default": (today - timedelta(days=1)).isoformat(),     # 오늘 - 1
            "cash": BACKTEST_CASH,
            "max_positions": MAX_POSITIONS,
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
            **strategy,  # signals, rule, period_days, resolution, execution
            "universe": _resolve_universe(form, data_folder),
            "start": form.get("start"), "end": form.get("end"),
            "cash": BACKTEST_CASH,  # 초기자본은 1억으로 고정(입력받지 않음)
            "max_positions": MAX_POSITIONS,  # 동시 보유 상한(균등분할 가능하게)
            "data_folder": data_folder,  # 분봉 적재 여부 스캔(장중 타점/시가 폴백 판단)용
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

    @app.get("/universe/index/{name}")
    def universe_index(name: str):
        # 인덱스(KOSPI200/KOSDAQ150) 구성종목을 한 번에 추가하기 위한 조회.
        # 적재된 종목과 교집합만 반환(백테스트 가능한 것만). KRX 로그인이 필요할 수 있음.
        from etl.universe import INDEX_CODES, list_universe
        from etl.catalog import list_price_tickers
        key = name.upper()
        if key not in INDEX_CODES:
            return {"error": f"지원하지 않는 인덱스: {name}", "tickers": []}
        try:
            members = list_universe(key)
        except Exception as e:
            return {"error": f"구성종목 조회 실패({type(e).__name__}) — KRX 로그인(설정) 필요할 수 있음",
                    "tickers": []}
        loaded = set(list_price_tickers(config.get_data_folder()))
        tickers = [t for t in members if t in loaded] if loaded else list(members)
        return {"index": key, "tickers": tickers, "total": len(members), "available": len(tickers)}

    @app.get("/ui/runs/{run_id}", response_class=HTMLResponse)
    def ui_run_detail(request: Request, run_id: str):
        record = store.get_run(run_id)
        if record is None:
            raise HTTPException(status_code=404, detail=f"run not found: {run_id}")
        from etl.names import load_names
        reasons = parse_rule_reasons(record.get("run_dir"))
        names = load_names(config.get_data_folder())
        return templates.TemplateResponse(request, "run_detail.html", {
            "run": record, "summary": friendly_stats(record.get("statistics") or {}),
            "trades": parse_orders(record.get("result_json"), reasons, names)})

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
            "execution_styles": signals_catalog.EXECUTION_STYLES,
            "param_value": signals_catalog.param_value,
            "risk": config.risk_form_values(),
            "data_loaded": _loaded_count(),
            "saved_exists": config.get_strategy() is not None,
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
        resolution, execution = signals_catalog.execution_from_form(form)
        spec = {
            "signals": signals_catalog.signals_from_form(form),
            "rule": rule,
            "groups": groups,
            "period_days": int(form.get("period_days") or signals_catalog.DEFAULT_PERIOD_DAYS),
            # ②층: 해상도(일봉/분봉) + 장중 체결 타이밍. 백테스트 스펙으로 그대로 전달된다.
            "resolution": resolution,
            "execution": execution,
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
        from etl.names import load_names
        names = load_names(data_dir)
        price = set(catalog.list_price_tickers(data_dir))
        flow = set(catalog.list_flow_tickers(data_dir))
        tickers = [{"ticker": t, "name": names.get(t, ""), "price": t in price, "flow": t in flow}
                   for t in sorted(price | flow)]
        return templates.TemplateResponse(request, "data_list.html", {
            "tickers": tickers, "count": len(tickers), "data_dir": data_dir,
            "latest_date": catalog.latest_loaded_date(data_dir),
            "error": request.query_params.get("error"),
        })

    @app.get("/data/{ticker}", response_class=HTMLResponse)
    def data_detail(request: Request, ticker: str):
        from etl import catalog
        from etl.names import load_names
        data_dir = config.get_data_folder()
        # 전체 날짜를 최신순으로(스크롤 + 날짜 필터는 화면에서). 한 종목이라 비용 작음.
        price = list(reversed(catalog.read_price_daily(data_dir, ticker)))
        flow = list(reversed(catalog.read_flow(data_dir, ticker)))
        return templates.TemplateResponse(request, "data_detail.html", {
            "ticker": ticker, "name": load_names(data_dir).get(ticker, ""),
            "price": price, "flow": flow})

    @app.post("/data/update")
    def update_data(request: Request):
        # '데이터 최신화' — 전체 시장(OHLCV+수급)을 마지막 적재일 다음날부터 증분 적재.
        # 스케줄러와 동일한 작업(orchestrator.data_tasks.run_data_update)을 백그라운드로.
        from ..data_tasks import run_data_update
        data_dir = config.get_data_folder()
        jobs.submit("데이터 최신화", lambda job: run_data_update(job, data_dir))
        return RedirectResponse(url="/jobs", status_code=303)

    @app.post("/data/minute")
    async def update_minute(request: Request):
        # 누적 분봉 적재(증권사 API) — 선택 종목/인덱스의 최근 N일 분봉을 백그라운드 적재.
        # 백테스트 장중 타이밍용 데이터. KIS 보관 한계로 최대 약 1년.
        from ..data_tasks import run_minute_update
        form = await request.form()
        tickers, errors = _resolve_minute_tickers(form)
        if not tickers:
            msg = errors[0] if errors else "분봉 적재할 종목/인덱스를 선택하세요"
            return RedirectResponse(url=f"/data?error={msg}", status_code=303)
        days = int(form.get("days") or 365)
        data_dir = config.get_data_folder()
        jobs.submit(f"분봉 적재 ({len(tickers)}종목)",
                    lambda job: run_minute_update(job, data_dir, tickers, days))
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
        from etl.catalog import latest_loaded_date
        data_dir = config.get_data_folder()
        broker = config.get_broker()
        return templates.TemplateResponse(request, "settings.html", {
            "secrets": config.secret_status(),
            "broker": broker,
            "brokers": config.BROKERS,
            "broker_secrets": config.broker_secret_status(broker),
            "data_dir": data_dir,
            "data_loaded": _loaded_count(),
            "latest_date": latest_loaded_date(data_dir),
            "saved": request.query_params.get("saved"),
            "error": request.query_params.get("error"),
        })

    @app.post("/settings")
    async def settings_save(request: Request):
        # 폼은 시크릿 키별 입력(동적) + 브로커 선택. save_secrets가 유효 키/빈값을 필터.
        form = await request.form()
        broker = form.get("broker")
        if broker in config.BROKERS:
            config.set_broker(broker)
        config.save_secrets({k: str(v) for k, v in form.items()})
        return RedirectResponse(url="/settings?saved=1", status_code=303)

    @app.post("/settings/test/krx")
    def settings_test_krx():
        # KRX 자격증명으로 펀더멘털 한 건을 실제 조회해 연동을 확인(무거우면 몇 초).
        if not config.apply_krx_credentials():
            return {"ok": False, "message": "KRX 아이디/비밀번호를 먼저 저장하세요"}
        try:
            from pykrx import stock
            day = stock.get_nearest_business_day_in_a_week()
            df = stock.get_market_fundamental_by_ticker(day, market="KOSPI")
            n = 0 if df is None else len(df)
            if n:
                return {"ok": True, "message": f"정상 — {day} 기준 {n}종목 지표 조회됨"}
            return {"ok": False, "message": "조회 결과가 비었습니다(로그인/권한 확인)"}
        except Exception as e:
            return {"ok": False, "message": f"실패: {type(e).__name__} {e}"}

    @app.post("/settings/test/kis")
    def settings_test_kis():
        # KIS App Key/Secret로 토큰을 실제 발급해 인증을 확인.
        cred = config.get_kis_credentials()
        if not (cred["app_key"] and cred["app_secret"]):
            return {"ok": False, "message": "KIS App Key/Secret을 먼저 저장하세요"}
        try:
            from brokers.kis import KisClient
            KisClient(cred["app_key"], cred["app_secret"], env="real").access_token()
            return {"ok": True, "message": "정상 — 접근토큰 발급 성공"}
        except Exception as e:
            return {"ok": False, "message": f"실패: {type(e).__name__} {e}"}
