"""KRX 유니버스 대량 OHLCV 적재.

종목마다 1회 호출(2,786종목=2,786콜, 느림/차단위험) 대신 **날짜별 단면**
(`get_market_ohlcv_by_ticker`)으로 한 번에 전 종목을 받아 효율 적재한다. 1년치 ≈ 거래일수(~250)
콜로 유니버스 전체 커버. 단면엔 시가총액도 있어(현재 미저장) 후속 유니버스 필터에 쓸 수 있다.

수급(투자자별)은 저렴한 일별 단면 API가 없어 per-ticker(etl.flow)로 따로 — 실제 매매 종목 위주로.
"""

from __future__ import annotations

import argparse
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


def ingest_all_market(
    data_dir: str | Path = DEFAULT_DATA_DIR, *, start: date | None = None,
    end: date | None = None, with_flow: bool = True, merge: bool = False, on_progress=None,
) -> dict[str, Any]:
    """한국시장 전체(KOSPI+KOSDAQ)의 OHLCV·수급을 적재.

    start 미지정 시 최근 DEFAULT_LOAD_YEARS년(최초 적재). merge=True면 기존 데이터에 증분 병합
    (데이터 최신화), False면 덮어쓰기. 수급은 KRX 로그인 시에만 best-effort(실패는 건너뜀).
    """
    from datetime import timedelta

    from etl.flow import ingest_flow
    from orchestrator.config import apply_krx_credentials

    def progress(msg):
        if on_progress:
            on_progress(msg)

    end = end or date.today()
    start = start or (end - timedelta(days=365 * DEFAULT_LOAD_YEARS))
    if start > end:  # 이미 최신 — 받을 구간 없음
        progress(f"이미 최신 ({start} 이후 새 데이터 없음)")
        return {"price_tickers": 0, "trading_days": 0, "flow_enabled": False,
                "flow_ok": 0, "flow_fail": 0, "start": start.isoformat(), "end": end.isoformat()}
    progress(f"적재 구간 {start} ~ {end} ({'증분' if merge else '전체'})")

    price = ingest_universe(start, end, market="ALL", data_dir=data_dir, merge=merge,
                            on_progress=on_progress)

    # 신규 거래일이 없으면(주말/휴장 구간 등) 수급은 받지 않는다. 안 그러면 전 종목에 대해
    # 빈 응답이 와서 pykrx가 종목마다 에러를 찍는다(거래일 0인데 수급만 호출하던 버그).
    flow_ok = flow_fail = 0
    flow_enabled = False
    if not with_flow:
        progress("수급 비활성")
    elif price["trading_days"] == 0:
        progress("신규 거래일 없음 — 수급 생략")
    elif not apply_krx_credentials():
        progress("수급 건너뜀 (KRX 로그인 없음 — 설정에서 키 입력 시 가능)")
    else:
        flow_enabled = True
        tickers = list_universe("ALL", end)
        progress(f"수급 적재 시작: {len(tickers)}종목 (종목당 1회 호출 → 시간 소요)")
        for i, tkr in enumerate(tickers, 1):
            try:
                ingest_flow(tkr, start, end, data_dir, merge=merge)
                flow_ok += 1
            except Exception:  # 개별 종목 실패(데이터 없음/상폐 등)는 건너뛰고 계속
                flow_fail += 1
            if i % 50 == 0 or i == len(tickers):
                progress(f"수급 {i}/{len(tickers)} (성공 {flow_ok}, 실패 {flow_fail})")

    progress(f"완료: OHLCV {price['ingested']}종목 × {price['trading_days']}거래일, 수급 {flow_ok}종목")
    return {
        "price_tickers": price["ingested"],
        "trading_days": price["trading_days"],
        "flow_enabled": flow_enabled,
        "flow_ok": flow_ok,
        "flow_fail": flow_fail,
        "start": start.isoformat(),
        "end": end.isoformat(),
    }


def update_all_market(data_dir: str | Path = DEFAULT_DATA_DIR, *, on_progress=None) -> dict[str, Any]:
    """데이터 최신화 — 적재된 마지막 날짜 다음날~오늘을 전체 시장 증분 적재.

    적재 이력이 없으면 최근 DEFAULT_LOAD_YEARS년 최초 적재. 대시보드 버튼과 스케줄러가 공유한다.
    """
    from datetime import timedelta
    from etl.catalog import latest_loaded_date
    last = latest_loaded_date(data_dir)
    if last:
        start = date.fromisoformat(last) + timedelta(days=1)
        return ingest_all_market(data_dir, start=start, merge=True, on_progress=on_progress)
    return ingest_all_market(data_dir, merge=False, on_progress=on_progress)  # 최초 적재


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
