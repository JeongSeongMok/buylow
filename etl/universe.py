"""KRX 유니버스 대량 OHLCV 적재.

종목마다 1회 호출(2,786종목=2,786콜, 느림/차단위험) 대신 **날짜별 단면**
(`get_market_ohlcv_by_ticker`)으로 한 번에 전 종목을 받아 효율 적재한다. 1년치 ≈ 거래일수(~250)
콜로 유니버스 전체 커버. 단면엔 시가총액도 있어(현재 미저장) 후속 유니버스 필터에 쓸 수 있다.

수급(투자자별)은 저렴한 일별 단면 API가 없어 per-ticker(etl.flow)로 따로 — 실제 매매 종목 위주로.
"""

from __future__ import annotations

import argparse
import contextlib
import io
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any

from market.krx import KRX_MARKET, inject_krx_market

from .lean_format import write_equity_daily
from .sources import Bar

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA_DIR = REPO_ROOT / "data"

# pykrx 지수코드 (지수 구성종목 조회용). 표준 KRX 코드.
INDEX_CODES = {"KOSPI200": "1028", "KOSDAQ150": "2203"}
KOSPI200_INDEX = INDEX_CODES["KOSPI200"]  # 하위호환


def list_universe(market: str = "KOSPI200", on: date | None = None) -> list[str]:
    """유니버스 종목코드 목록. market: KOSPI200 | KOSDAQ150 | KOSPI | KOSDAQ | ALL."""
    from pykrx import stock
    from orchestrator.config import apply_krx_credentials
    apply_krx_credentials()  # 지수 구성종목 조회 등은 KRX 로그인 필요
    on_str = (on or date.today()).strftime("%Y%m%d")
    code = INDEX_CODES.get(market.upper())
    if code:  # KOSPI200/KOSDAQ150 등 지수 → 구성종목(deposit file). 날짜 명시(오늘 기준 실패 방지)
        return list(stock.get_index_portfolio_deposit_file(code, date=on_str))
    return list(stock.get_market_ticker_list(on_str, market=market.upper()))


def ingest_universe(
    start: date, end: date, market: str = "KOSPI200", data_dir: str | Path = DEFAULT_DATA_DIR,
    merge: bool = True, on_progress=None,
) -> dict[str, Any]:
    """유니버스 전 종목의 일봉을 날짜별 단면으로 받아 LEAN 포맷으로 적재.

    merge=True면 기존 파일과 증분 병합(스케줄러 일일 갱신). merge=False면 덮어쓰기(전체 재적재).
    on_progress(msg)가 주어지면 진행 상황을 콜백으로 알린다(대시보드 작업 로그용).
    """
    from pykrx import stock

    def progress(msg):
        if on_progress:
            on_progress(msg)

    universe = set(list_universe(market, end))
    # 실제 거래일만 순회(휴장일 콜 낭비/중복 방지) — 기준 종목(삼성)의 거래일 인덱스 사용
    cal = stock.get_market_ohlcv_by_date(start.strftime("%Y%m%d"), end.strftime("%Y%m%d"), "005930")
    trading_days = [ts.date() for ts in cal.index]
    total = len(trading_days)
    progress(f"OHLCV 적재 시작: {market} {len(universe)}종목 × {total}거래일 ({start}~{end})")

    series: dict[str, list[Bar]] = defaultdict(list)
    for i, d in enumerate(trading_days, 1):
        df = stock.get_market_ohlcv_by_ticker(d.strftime("%Y%m%d"), market="ALL")
        if df is not None and not df.empty:
            for tkr, r in df.iterrows():
                if tkr in universe and r["종가"] > 0:
                    series[tkr].append(Bar(
                        d, float(r["시가"]), float(r["고가"]), float(r["저가"]),
                        float(r["종가"]), int(r["거래량"]),
                    ))
        if i % 20 == 0 or i == total:  # 너무 잦지 않게 20거래일마다 진행 보고
            progress(f"OHLCV 수집 {i}/{total}거래일")

    progress(f"OHLCV 파일 기록 중… ({len(series)}종목)")
    for tkr, bars in series.items():
        write_equity_daily(data_dir, KRX_MARKET, tkr, bars, merge=merge)
    inject_krx_market(data_dir)
    progress(f"OHLCV 적재 완료: {len(series)}종목")

    return {
        "market": market,
        "universe": len(universe),
        "ingested": len(series),
        "trading_days": len(trading_days),
    }


DEFAULT_LOAD_YEARS = 5  # 전체 적재 버튼 기본 기간 (백테스트에 충분한 과거 구간)


def _trading_days(start: date, end: date) -> list[date]:
    """[start,end] 실제 거래일 목록(005930 캘린더, 무인증). 주말/휴장 구간 가드용."""
    from pykrx import stock
    cal = stock.get_market_ohlcv_by_date(start.strftime("%Y%m%d"), end.strftime("%Y%m%d"), "005930")
    return [ts.date() for ts in cal.index]


def _quiet(fn, *args, **kwargs):
    """pykrx 호출의 stdout/stderr를 억제. 데이터 없는 종목(상폐·ETF·우선주 등)마다 pykrx가
    'Error occurred in ...'를 찍어 콘솔이 폭주하므로 출력만 막는다(예외는 그대로 전파→상위에서 카운트)."""
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        return fn(*args, **kwargs)


def _ingest_per_ticker(tickers, end, data_dir, *, merge, on_progress,
                       flow_start=None, fund_start=None):
    """종목별 수급/펀더멘털 적재. 시작일이 None인 종류는 생략. 개별 실패는 건너뛰고 계속."""
    from etl.flow import ingest_flow
    from etl.fundamental import ingest_fundamental
    fok = ffail = uok = ufail = 0
    for i, tkr in enumerate(tickers, 1):
        if flow_start is not None:
            try:
                _quiet(ingest_flow, tkr, flow_start, end, data_dir, merge=merge); fok += 1
            except Exception:
                ffail += 1
        if fund_start is not None:
            try:
                _quiet(ingest_fundamental, tkr, fund_start, end, data_dir, merge=merge); uok += 1
            except Exception:
                ufail += 1
        if on_progress and (i % 50 == 0 or i == len(tickers)):
            on_progress(f"수급/펀더멘털 {i}/{len(tickers)} (수급 {fok}, 펀더 {uok})")
    return fok, ffail, uok, ufail


def ingest_all_market(
    data_dir: str | Path = DEFAULT_DATA_DIR, *, start: date | None = None,
    end: date | None = None, with_flow: bool = True, merge: bool = False, on_progress=None,
) -> dict[str, Any]:
    """한국시장 전체(KOSPI+KOSDAQ)의 OHLCV·수급·펀더멘털을 한 구간으로 적재(최초/전체 적재용).

    종류별 증분은 update_all_market을 쓴다. start 미지정 시 최근 DEFAULT_LOAD_YEARS년.
    수급·펀더멘털은 KRX 로그인 시에만 best-effort(실패는 건너뜀).
    """
    from datetime import timedelta
    from orchestrator.config import apply_krx_credentials

    def progress(msg):
        if on_progress:
            on_progress(msg)

    end = end or date.today()
    start = start or (end - timedelta(days=365 * DEFAULT_LOAD_YEARS))
    progress(f"적재 구간 {start} ~ {end} ({'증분' if merge else '전체'})")
    price = ingest_universe(start, end, market="ALL", data_dir=data_dir, merge=merge,
                            on_progress=on_progress)

    fok = ffail = uok = ufail = 0
    enabled = False
    if not with_flow:
        progress("수급/펀더멘털 비활성")
    elif price["trading_days"] == 0:  # 주말/휴장 구간 → 빈 응답 폭주 방지
        progress("신규 거래일 없음 — 수급/펀더멘털 생략")
    elif not apply_krx_credentials():
        progress("수급/펀더멘털 건너뜀 (KRX 로그인 없음 — 설정에서 키 입력 시 가능)")
    else:
        enabled = True
        tickers = list_universe("ALL", end)
        progress(f"수급·펀더멘털 적재 시작: {len(tickers)}종목 (종목당 2회 호출 → 시간 소요)")
        fok, ffail, uok, ufail = _ingest_per_ticker(
            tickers, end, data_dir, merge=merge, on_progress=on_progress,
            flow_start=start, fund_start=start)

    progress(f"완료: OHLCV {price['ingested']}종목, 수급 {fok}종목, 펀더 {uok}종목")
    return {"price_tickers": price["ingested"], "trading_days": price["trading_days"],
            "flow_enabled": enabled, "flow_ok": fok, "flow_fail": ffail,
            "fund_ok": uok, "fund_fail": ufail, "start": start.isoformat(), "end": end.isoformat()}


def update_all_market(data_dir: str | Path = DEFAULT_DATA_DIR, *, on_progress=None) -> dict[str, Any]:
    """데이터 최신화 — 가격·수급·펀더멘털을 **각자** 마지막 적재일 다음날~오늘로 증분.

    어떤 종류가 비어 있으면 그 종류만 최근 DEFAULT_LOAD_YEARS년 백필(다른 종류는 증분 그대로).
    대시보드 버튼과 스케줄러가 공유한다.
    """
    from datetime import timedelta
    from etl.catalog import latest_loaded_date
    from orchestrator.config import apply_krx_credentials

    def progress(msg):
        if on_progress:
            on_progress(msg)

    end = date.today()
    boot = end - timedelta(days=365 * DEFAULT_LOAD_YEARS)

    def gap_start(kind):
        last = latest_loaded_date(data_dir, kind)
        return (date.fromisoformat(last) + timedelta(days=1)) if last else boot

    # OHLCV (가격) — 가격 갭만큼만
    p_start = gap_start("price")
    if p_start <= end:
        price = ingest_universe(p_start, end, market="ALL", data_dir=data_dir, merge=True,
                                on_progress=on_progress)
    else:
        progress("OHLCV 이미 최신")
        price = {"ingested": 0, "trading_days": 0}

    # 수급/펀더멘털 — 각자 갭만큼. 거래일 없는(주말 등) 종류는 생략(빈 응답 폭주 방지).
    fok = ffail = uok = ufail = 0
    enabled = False
    f_start, u_start = gap_start("flow"), gap_start("fundamental")
    if not apply_krx_credentials():
        progress("수급/펀더멘털 건너뜀 (KRX 로그인 없음 — 설정에서 키 입력 시 가능)")
    else:
        tdays = _trading_days(min(f_start, u_start), end)
        flow_start = f_start if any(d >= f_start for d in tdays) else None
        fund_start = u_start if any(d >= u_start for d in tdays) else None
        if flow_start is None and fund_start is None:
            progress("수급/펀더멘털 신규 거래일 없음 — 생략")
        else:
            enabled = True
            tickers = list_universe("ALL", end)
            progress(f"수급/펀더멘털 적재: {len(tickers)}종목 "
                     f"(수급 {flow_start or '생략'}~, 펀더 {fund_start or '생략'}~)")
            fok, ffail, uok, ufail = _ingest_per_ticker(
                tickers, end, data_dir, merge=True, on_progress=on_progress,
                flow_start=flow_start, fund_start=fund_start)

    progress(f"완료: OHLCV {price['ingested']}종목, 수급 {fok}종목, 펀더 {uok}종목")
    return {"price_tickers": price["ingested"], "trading_days": price["trading_days"],
            "flow_enabled": enabled, "flow_ok": fok, "flow_fail": ffail,
            "fund_ok": uok, "fund_fail": ufail, "end": end.isoformat()}


def update_universe(market: str = "KOSPI200", data_dir: str | Path = DEFAULT_DATA_DIR,
                    days: int = 5) -> dict[str, Any]:
    """최근 days일을 받아 증분 병합 (단일 유니버스용 경량 유틸). 멱등."""
    from datetime import timedelta
    end = date.today()
    return ingest_universe(end - timedelta(days=days), end, market, data_dir)


def main() -> None:
    p = argparse.ArgumentParser(prog="python -m etl.universe", description="KRX 유니버스 대량 OHLCV 적재")
    p.add_argument("--market", default="KOSPI200", help="KOSPI200 | KOSPI | KOSDAQ | ALL")
    p.add_argument("--from", dest="start", required=True, type=date.fromisoformat)
    p.add_argument("--to", dest="end", default=None, type=date.fromisoformat, help="기본: 오늘")
    p.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    args = p.parse_args()
    info = ingest_universe(args.start, args.end or date.today(), args.market, Path(args.data_dir))
    print(f"유니버스 적재 완료: {info['market']} {info['ingested']}/{info['universe']}종목 "
          f"× {info['trading_days']}거래일")


if __name__ == "__main__":
    main()
