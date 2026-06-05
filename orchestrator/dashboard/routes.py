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


# ── 거래내역 캐시 + 페이지네이션 ──────────────────────────────────────────
# 분봉 백테스트는 거래가 6만+ 건이 되기도 한다. 그때마다 결과 JSON(수십~수백 MB)을 통째로
# json.loads 하면 상세 페이지 진입이 매우 느리다. 그래서 한 번만 파싱해 거래내역을 슬림한
# trades.jsonl(한 줄=한 거래, 최신순)로 캐시하고, 화면은 거기서 필요한 페이지 슬라이스만 읽는다
# (완료된 run은 불변이라 1회 빌드가 안전 — '한 뎁스 추가' + 페이지네이션).

def _trades_cache_path(record: dict) -> Path | None:
    run_dir = record.get("run_dir")
    if run_dir:
        return Path(run_dir) / "trades.jsonl"
    rj = record.get("result_json")
    return Path(rj).parent / "trades.jsonl" if rj else None


def _ensure_trades_cache(record: dict, reasons=None, names=None) -> Path | None:
    """trades.jsonl 캐시를 보장(없으면 결과 JSON을 1회 파싱해 최신순으로 기록)."""
    cache = _trades_cache_path(record)
    if cache is None:
        return None
    if cache.exists():
        return cache
    rows = parse_orders(record.get("result_json"), reasons, names)  # 1회 풀 파싱
    rows.reverse()  # 최신순(시간 내림차순) 저장 → offset=0이 가장 최근
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        with open(cache, "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
    except OSError:
        return None
    return cache


def load_trades_page(record: dict, offset: int, limit: int,
                     reasons=None, names=None) -> tuple[list[dict], int]:
    """거래내역 한 페이지 + 전체 건수. 캐시(trades.jsonl)에서 [offset:offset+limit]만 파싱한다."""
    cache = _ensure_trades_cache(record, reasons, names)
    if cache is None or not cache.exists():
        return [], 0
    rows, total = [], 0
    with open(cache, encoding="utf-8") as f:
        for i, line in enumerate(f):
            total += 1
            if offset <= i < offset + limit:
                rows.append(json.loads(line))
    return rows, total


def _delete_run_dir(run_dir) -> None:
    """run의 디스크 blob 디렉터리 삭제. 실수로 엉뚱한 경로를 지우지 않게 repo의 runs/ 하위만 허용."""
    if not run_dir:
        return
    import shutil
    runs_root = (REPO_ROOT / "runs").resolve()
    try:
        p = Path(run_dir).resolve()
    except OSError:
        return
    if p.is_dir() and runs_root in p.parents:
        shutil.rmtree(p, ignore_errors=True)


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
    trade_store: Any = None,
    get_broker: Callable[[], tuple] | None = None,
    broker_cache: Any = None,
    live_manager: Any = None,
) -> None:
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    # 매매 탭 의존성 — 주입 안 되면 기본(SQLite TradeStore + KIS 조회 브로커 + 메모리 캐시 + 라이브 매니저).
    if trade_store is None:
        from ..persistence import TradeStore
        trade_store = TradeStore()
    if get_broker is None:
        from brokers.kis_broker import get_trading_broker as get_broker
    if broker_cache is None:
        from ..broker_cache import BrokerCache
        broker_cache = BrokerCache(get_broker)
    if live_manager is None:
        from ..live_runner import LiveProcessManager
        live_manager = LiveProcessManager(jobs)
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
            "indices": config.all_indices(),  # 내장+커스텀 인덱스 버튼 동적 렌더
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
        # 인덱스(내장 KOSPI200/KOSDAQ150 또는 커스텀 종목 그룹) 구성종목을 한 번에 추가하기 위한 조회.
        # 적재된 종목과 교집합만 반환(백테스트 가능한 것만).
        from etl.universe import INDEX_CODES, index_members_cached
        from etl.catalog import list_price_tickers
        loaded = set(list_price_tickers(config.get_data_folder()))
        # 커스텀 인덱스 우선 — 저장된 종목을 그대로 쓴다(pykrx 조회 불필요).
        custom = config.get_custom_indices()
        if name in custom:
            members = custom[name].get("tickers", [])
            tickers = [t for t in members if t in loaded] if loaded else list(members)
            return {"index": name, "tickers": tickers, "total": len(members),
                    "available": len(tickers), "custom": True}
        key = name.upper()
        if key not in INDEX_CODES:
            return {"error": f"지원하지 않는 인덱스: {name}", "tickers": []}
        try:
            # 디스크 캐시 우선 — 구성종목은 분기 단위로만 바뀌므로 매 클릭 KRX 재조회를 피한다.
            members = index_members_cached(key, config.get_data_folder())
        except Exception as e:
            return {"error": f"구성종목 조회 실패({type(e).__name__}) — KRX 로그인(설정) 필요할 수 있음",
                    "tickers": []}
        tickers = [t for t in members if t in loaded] if loaded else list(members)
        return {"index": key, "tickers": tickers, "total": len(members), "available": len(tickers)}

    @app.get("/groups", response_class=HTMLResponse)
    def groups_page(request: Request):
        # 커스텀 인덱스(종목 묶음) 관리 전용 탭. 데이터/백테스트는 결과를 '사용'만 하고 관리는 여기서.
        from etl.names import load_names
        return templates.TemplateResponse(request, "groups.html", {
            "names": load_names(config.get_data_folder()),  # 종목 검색/칩 표시용
            "custom_indices": config.get_custom_indices(),
            "saved": request.query_params.get("saved"),
            "error": request.query_params.get("error"),
        })

    @app.post("/universe/custom")
    async def universe_custom_create(request: Request):
        # 커스텀 인덱스 생성/수정. '그룹' 탭에서 호출. original_key가 있고 이름이 바뀌었으면 rename
        # (옛 키 삭제 후 새 이름으로 저장). 같은 이름이면 단순 덮어쓰기.
        form = await request.form()
        name = (form.get("name") or "").strip()
        original = (form.get("original_key") or "").strip()
        try:
            config.save_custom_index(name, form.get("universe") or "")
        except ValueError as e:
            return RedirectResponse(url=f"/groups?error={e}", status_code=303)
        if original and original != name:
            config.delete_custom_index(original)  # 이름 변경 → 옛 그룹 제거
        return RedirectResponse(url="/groups?saved=1", status_code=303)

    @app.post("/universe/custom/delete")
    async def universe_custom_delete(request: Request):
        form = await request.form()
        config.delete_custom_index(form.get("key") or "")
        return RedirectResponse(url="/groups", status_code=303)

    @app.get("/ui/runs/{run_id}", response_class=HTMLResponse)
    def ui_run_detail(request: Request, run_id: str):
        record = store.get_run(run_id)
        if record is None:
            raise HTTPException(status_code=404, detail=f"run not found: {run_id}")
        # 신호 진단(추정) — 왜 매수/매도가 적었는지 경향(매수신호 발생률 + 차단 신호).
        # 실패해도(데이터 없음 등) 페이지엔 영향 없음(diag=None이면 섹션 미표시).
        diag = None
        try:
            rs = (record.get("parameters") or {}).get("rule_spec")
            spec = json.loads(rs) if rs else {}
            from ..signal_diag import analyze_run
            diag = analyze_run(spec, config.get_data_folder())
        except Exception:
            diag = None
        # 거래내역은 6만+ 건이 될 수 있어 인라인으로 안 싣고, 아래 /ui/runs/{id}/trades 에서 HTMX로
        # 페이지네이션해 가져온다(상세 진입 시 결과 JSON 통째 파싱 회피).
        return templates.TemplateResponse(request, "run_detail.html", {
            "run": record, "summary": friendly_stats(record.get("statistics") or {}),
            "diag": diag, "trade_limit": 100})

    @app.get("/ui/runs/{run_id}/trades", response_class=HTMLResponse)
    def ui_run_trades(request: Request, run_id: str, offset: int = 0, limit: int = 100):
        record = store.get_run(run_id)
        if record is None:
            raise HTTPException(status_code=404, detail=f"run not found: {run_id}")
        from etl.names import load_names
        limit = max(1, min(int(limit), 500))  # 페이지 크기 상한(DOM 폭주 방지)
        offset = max(0, int(offset))
        reasons = parse_rule_reasons(record.get("run_dir"))
        names = load_names(config.get_data_folder())
        rows, total = load_trades_page(record, offset, limit, reasons, names)
        return templates.TemplateResponse(request, "partials/trades_table.html", {
            "run": record, "trades": rows, "total": total, "offset": offset, "limit": limit,
            "next_offset": offset + limit, "prev_offset": max(0, offset - limit),
            "has_next": offset + limit < total, "has_prev": offset > 0,
            "start1": (offset + 1) if total else 0, "end1": min(offset + limit, total)})

    @app.post("/ui/runs/clear")
    def ui_runs_clear(request: Request):
        # 백테스트 히스토리 전체 삭제(DB 행 + 디스크 blob). 되돌릴 수 없다(화면에서 confirm).
        for r in store.list_runs(limit=100000):
            _delete_run_dir(r.get("run_dir"))
        store.clear_runs()
        return RedirectResponse(url="/backtest", status_code=303)

    @app.post("/ui/runs/{run_id}/delete")
    def ui_run_delete(request: Request, run_id: str):
        # 백테스트 1건 삭제(DB 행 + 디스크 blob). 결과 JSON·로그가 GB 단위라 디스크도 정리한다.
        record = store.get_run(run_id)
        if record is not None:
            _delete_run_dir(record.get("run_dir"))
            store.delete_run(run_id)
        return RedirectResponse(url="/backtest", status_code=303)

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
        minute = set(catalog.list_minute_tickers(data_dir))
        tickers = [{"ticker": t, "name": names.get(t, ""), "price": t in price, "flow": t in flow,
                    "minute": (catalog.minute_day_count(data_dir, t) if t in minute else 0),
                    "minute_latest": (catalog.minute_latest_date(data_dir, t) if t in minute else None)}
                   for t in sorted(price | flow | minute)]
        broker = config.get_broker()
        return templates.TemplateResponse(request, "data_list.html", {
            "tickers": tickers, "count": len(tickers), "data_dir": data_dir,
            "names": names,  # 분봉 적재 종목 검색/칩 UX용 (백테스트와 동일)
            "indices": config.all_indices(),  # 내장+커스텀 — 분봉적재 버튼 + 적재현황 필터(사용만)
            "loaded_codes": [t["ticker"] for t in tickers],  # [전체종목] 일괄 칩 추가용(적재된 전 종목)
            "latest_date": catalog.latest_loaded_date(data_dir),
            # 분봉 적재는 증권사 API를 쓰므로(데이터 최신화는 pykrx·KRX로 증권사 무관) 활성 증권사를 표시.
            "broker": broker,
            "broker_label": config.BROKER_LABELS.get(broker, broker),
            # 자동 스케줄러 상태 + 분봉 자동적재 대상종목(별도 설정)
            "scheduler": config.get_scheduler_config(),
            "error": request.query_params.get("error"),
            "saved": request.query_params.get("saved"),
        })

    @app.get("/data/{ticker}", response_class=HTMLResponse)
    def data_detail(request: Request, ticker: str):
        from etl import catalog
        from etl.names import load_names
        from etl.lean_format import list_minute_days, read_equity_minute
        from market.krx import KRX_MARKET
        data_dir = config.get_data_folder()
        # 전체 날짜를 최신순으로(스크롤 + 날짜 필터는 화면에서). 한 종목이라 비용 작음.
        price = list(reversed(catalog.read_price_daily(data_dir, ticker)))
        flow = list(reversed(catalog.read_flow(data_dir, ticker)))
        # 분봉: 적재된 날짜 목록(최신순) + 선택한 하루치 분봉(기본 최신일). 분봉은 하루 ~390개라
        # 하루 단위로만 보여준다(전체를 한 번에 그리면 무거움).
        minute_days = sorted(list_minute_days(data_dir, KRX_MARKET, ticker), reverse=True)
        minute_days_iso = [d.isoformat() for d in minute_days]
        sel = request.query_params.get("minute")
        if sel not in minute_days_iso:
            sel = minute_days_iso[0] if minute_days_iso else None
        minute_bars = []
        if sel:
            from datetime import date as _date
            y, m, dd = sel.split("-")
            mb = read_equity_minute(data_dir, KRX_MARKET, ticker, _date(int(y), int(m), int(dd)))
            for b in mb:  # ms(자정기준) → HH:MM
                minute_bars.append({"time": f"{b.ms // 3600000:02d}:{(b.ms % 3600000) // 60000:02d}",
                                    "open": b.open, "high": b.high, "low": b.low,
                                    "close": b.close, "volume": b.volume})
        return templates.TemplateResponse(request, "data_detail.html", {
            "ticker": ticker, "name": load_names(data_dir).get(ticker, ""),
            "price": price, "flow": flow,
            "minute_days": minute_days_iso, "minute_sel": sel, "minute_bars": minute_bars})

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

    @app.post("/data/schedule/minute")
    async def save_schedule_minute(request: Request):
        # 자동 스케줄러가 매 주기 분봉을 적재할 대상종목을 저장(분봉 적재 폼과 동일한 선택 UX).
        # 빈 선택이면 분봉 자동적재를 끄는 의미(일봉만 자동 적재).
        form = await request.form()
        tickers, errors = _resolve_minute_tickers(form)
        if errors and not tickers:
            return RedirectResponse(url=f"/data?error={errors[0]}", status_code=303)
        config.save_scheduler_minute_universe(tickers)
        return RedirectResponse(url="/data?saved=schedule", status_code=303)

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

    # ── 매매(라이브) 탭 ───────────────────────────────────────────────
    def _seoul_today() -> str:
        from datetime import datetime
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("Asia/Seoul")).date().isoformat()

    def _date_nav(sel_date: str) -> tuple[str, str]:
        """달력 전일/익일(체결조회는 임의 날짜 조회 가능). (prev, next)."""
        from datetime import date as _date, timedelta
        y, m, d = sel_date.split("-")
        cur = _date(int(y), int(m), int(d))
        return (cur - timedelta(days=1)).isoformat(), (cur + timedelta(days=1)).isoformat()

    def _trades_view(sel_date: str) -> dict:
        """매매내역 컨텍스트 — 메모리 캐시(백그라운드 갱신) 우선, 캐시 미스/브로커 없음 시 자체 거래로그."""
        rows, at = broker_cache.get_trades(sel_date)
        if rows is None:
            rows = trade_store.list_trades(sel_date)
            at = None
        prev_date, next_date = _date_nav(sel_date)
        return {
            "trades": rows, "sel_date": sel_date,
            "prev_date": prev_date, "next_date": next_date, "at": at,
            "daily_pnl": sum((t.get("realized_pnl") or 0) for t in rows),
            "has_pnl": any(t.get("realized_pnl") is not None for t in rows),
        }

    @app.get("/trade", response_class=HTMLResponse)
    def trade_page(request: Request):
        # 진입을 빠르게: 계좌(A)·장상태(E)만 서버에서 채우고, 잔고(B)·매매내역(C)은 비동기 로드한다
        # (아래 partial이 hx-trigger="load"로 가져옴 — 진입 시 'KIS 잔고+체결조회' 동기 대기를 없앤다).
        live = config.get_live_config()
        broker, broker_err = get_broker()
        account, market = None, None
        errors = {"account": None, "balance": None, "market": None}
        if broker is None:
            errors = {k: broker_err for k in errors}
        else:
            try: account = broker.account_info()
            except Exception as e: errors["account"] = f"{type(e).__name__}: {e}"
            try: market = broker.market_status()
            except Exception as e: errors["market"] = f"{type(e).__name__}: {e}"

        sel_date = request.query_params.get("date") or _seoul_today()
        prev_date, next_date = _date_nav(sel_date)

        # 라이브 자동매매: 대상종목(유니버스) + 전략 저장여부 + 실제 프로세스 실행여부.
        from etl.names import load_names
        running = live_manager.is_running()
        # 실행 상태 라벨: 실제 프로세스가 돌면 running, 토글만 켜졌으면 idle, 아니면 off.
        if running:
            run_state = "running"
        elif live["enabled"]:
            run_state = "idle"
        else:
            run_state = "off"

        return templates.TemplateResponse(request, "trade.html", {
            "live": live, "account": account, "market": market,
            "errors": errors, "broker_ok": broker is not None,
            "loading": True,  # B/C는 '불러오는 중' 자리표시 → hx로 채움
            "balance": None, "trades": [], "sel_date": sel_date,
            "prev_date": prev_date, "next_date": next_date, "daily_pnl": 0, "has_pnl": False,
            "run_state": run_state, "running": running,
            # 라이브 유니버스 선택 UI(백테스트 패턴 재사용)
            "live_universe": config.get_live_universe(),
            "indices": config.all_indices(),
            "names": load_names(config.get_data_folder()),
            "has_strategy": config.get_strategy() is not None,
            "format_won": format_won,
            "saved": request.query_params.get("saved"),
            "error": request.query_params.get("error"),
        })

    @app.post("/trade/universe")
    async def trade_universe_save(request: Request):
        # 라이브 자동매매 대상종목 저장(매매 탭). 백테스트와 동일한 칩(csv) 형식.
        form = await request.form()
        tickers = [t.strip() for t in (form.get("universe") or "").split(",") if t.strip()]
        config.save_live_universe(tickers)
        return RedirectResponse(url="/trade?saved=1", status_code=303)

    @app.get("/trade/balance", response_class=HTMLResponse)
    def trade_balance(request: Request):
        # 잔고/보유종목 부분 — 메모리 캐시(백그라운드 갱신)에서 즉시 반환(KIS 왕복 없음).
        balance, err, at = broker_cache.get_balance()
        return templates.TemplateResponse(request, "partials/trade_balance.html", {
            "balance": balance, "errors": {"balance": err}, "at": at, "format_won": format_won})

    @app.get("/trade/trades", response_class=HTMLResponse)
    def trade_trades(request: Request):
        # 매매내역 부분 — 캐시 우선(당일은 백그라운드 갱신, 과거는 요청 시 캐시).
        sel_date = request.query_params.get("date") or _seoul_today()
        ctx = _trades_view(sel_date)
        ctx["format_won"] = format_won
        return templates.TemplateResponse(request, "partials/trade_trades.html", ctx)

    @app.post("/trade/toggle")
    async def trade_toggle(request: Request):
        # 자동매매 on/off → LEAN 라이브 프로세스 start/stop(킬 스위치).
        form = await request.form()
        want_on = str(form.get("enabled", "")).lower() in ("1", "true", "on", "yes")

        if not want_on:  # 끄기 → 라이브 프로세스 종료
            live_manager.stop()
            config.set_live_enabled(False)
            return RedirectResponse(url="/trade?saved=1", status_code=303)

        # 켜기 — 안전 가드(무장) + 전략·유니버스·어댑터 준비 확인.
        import json as _json
        config.set_live_enabled(True)  # arming_ok가 enabled를 보므로 먼저 설정
        ok, why = config.live_arming_ok()
        strategy = config.get_strategy()
        universe = config.get_live_universe()
        from ..lean.environment import LAUNCHER_OUT
        adapter_ok = (LAUNCHER_OUT / "MyTrading.Kis.dll").exists()

        def _fail(msg):
            config.set_live_enabled(False)
            return RedirectResponse(url=f"/trade?error={msg}", status_code=303)

        if not ok:
            return _fail(why)
        if strategy is None:
            return _fail("전략을 먼저 저장하세요(전략 설정 탭).")
        if not universe:
            return _fail("대상종목(유니버스)을 먼저 선택하세요.")
        if not adapter_ok:
            return _fail("KIS 어댑터가 없습니다 — 터미널에서 scripts/build-adapter.sh 로 먼저 빌드하세요.")

        # 저장된 전략 + 라이브 유니버스로 라이브 spec(백테스트의 start/end/cash는 없음 — 라이브는 무한·계좌잔액).
        spec = {**strategy, "universe": universe, "data_folder": config.get_data_folder()}
        req = RunRequest(
            strategy_path="strategies/RuleStrategy.py",
            data_folder=config.get_data_folder(),
            algorithm_type="RuleStrategy",
            parameters={"rule_spec": _json.dumps(spec)},
        )
        try:
            live_manager.start(get_runner(), req)
        except Exception as e:
            return _fail(f"라이브 시작 실패: {type(e).__name__} {e}")
        return RedirectResponse(url="/trade?saved=1", status_code=303)

    @app.post("/trade/arm")
    async def trade_arm(request: Request):
        # 무장/주문한도/HTS ID 저장. (실전/모의 환경은 증권사 선택이 결정 — 여기선 안 받음.)
        form = await request.form()
        config.save_live_config({
            "armed": str(form.get("armed", "")).lower() in ("1", "true", "on", "yes"),
            "max_order_amount": form.get("max_order_amount") or 0,
            "hts_id": form.get("hts_id") or "",
        })
        return RedirectResponse(url="/trade?saved=1", status_code=303)

    @app.get("/settings", response_class=HTMLResponse)
    def settings_page(request: Request):
        from etl.catalog import latest_loaded_date
        data_dir = config.get_data_folder()
        # 모든 동작(데이터·매매·연동테스트)은 '활성 증권사' 하나로 판단한다. 드롭다운 선택은 즉시
        # 활성 증권사를 바꾼다(/settings/broker) — 미리보기/미저장 같은 중간 상태가 없다.
        broker = config.get_broker()
        return templates.TemplateResponse(request, "settings.html", {
            "secrets": config.secret_status(),
            "broker": broker,
            "brokers": config.BROKERS,
            "broker_labels": config.BROKER_LABELS,
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
        broker_cache.invalidate()  # 키/증권사 변경 → 캐시 무효(다음 조회가 새로 채움)
        return RedirectResponse(url="/settings?saved=1", status_code=303)

    @app.post("/settings/broker")
    async def settings_set_broker(request: Request):
        # 드롭다운 선택 = 활성 증권사 즉시 전환. 이후 데이터·매매·연동테스트가 이 증권사 기준으로 동작.
        form = await request.form()
        b = form.get("broker")
        if b in config.BROKERS:
            config.set_broker(b)
            broker_cache.invalidate()  # 새 활성 증권사 데이터로 즉시 교체
        return RedirectResponse(url="/settings", status_code=303)

    @app.post("/settings/clear")
    def settings_clear():
        # 활성 증권사의 저장된 키를 삭제(실수 입력 정리·증권사 전환 시).
        config.clear_broker_secrets(config.get_broker())
        broker_cache.invalidate()
        return RedirectResponse(url="/settings", status_code=303)

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
        # 활성 증권사(실전/모의) 키로 그 환경 도메인에 토큰을 실제 발급해 인증을 확인한다.
        broker = config.get_broker()
        if broker not in ("kis", "kis_demo"):
            broker = "kis"
        label = config.BROKER_LABELS.get(broker, broker)
        cred = config.get_kis_credentials(broker)
        if not (cred["app_key"] and cred["app_secret"]):
            return {"ok": False, "message": f"{label} App Key/Secret을 먼저 저장하세요"}
        env = config.broker_env(broker)
        try:
            from brokers.kis import KisClient
            KisClient(cred["app_key"], cred["app_secret"], env=env).access_token()
            envlabel = "모의투자" if env == "demo" else "실전"
            return {"ok": True, "message": f"정상 — {label}({envlabel}) 접근토큰 발급 성공"}
        except Exception as e:
            return {"ok": False, "message": f"실패({label}): {type(e).__name__} {e}"}
