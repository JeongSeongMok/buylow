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

KOSPI200_INDEX = "1028"  # pykrx 지수코드: KOSPI200


def list_universe(market: str = "KOSPI200", on: date | None = None) -> list[str]:
    """유니버스 종목코드 목록. market: KOSPI200 | KOSPI | KOSDAQ | ALL."""
    from pykrx import stock
    from orchestrator.config import apply_krx_credentials
    apply_krx_credentials()  # 지수 구성종목 조회 등은 KRX 로그인 필요
    on_str = (on or date.today()).strftime("%Y%m%d")
    if market.upper() == "KOSPI200":
        # 날짜 미지정 시 '오늘' 기준이라 실패할 수 있어 명시적으로 전달
        return list(stock.get_index_portfolio_deposit_file(KOSPI200_INDEX, date=on_str))
    return list(stock.get_market_ticker_list(on_str, market=market.upper()))


def _write_universe_rank(data_dir: str | Path, series: dict[str, list[Bar]]) -> None:
    """거래대금(종가×거래량) 평균 기준으로 종목을 정렬해 랭킹 파일로 저장.

    '전체 종목 대상' 백테스트가 이 상위 N종목만 빠르게 고를 수 있게 한다(전 종목 동일비중은
    종목당 배분이 1주 미만이 돼 매매가 안 됨 → 유동성 상위로 제한).
    """
    rows = []
    for tkr, bars in series.items():
        if bars:
            rows.append((tkr, sum(b.close * b.volume for b in bars) / len(bars)))
    rows.sort(key=lambda r: r[1], reverse=True)
    out = Path(data_dir) / "krx" / "universe_rank.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(f"{t},{v:.0f}" for t, v in rows) + "\n", encoding="utf-8")


def ingest_universe(
    start: date, end: date, market: str = "KOSPI200", data_dir: str | Path = DEFAULT_DATA_DIR,
    merge: bool = True, on_progress=None, write_rank: bool = False,
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
    if write_rank:
        _write_universe_rank(data_dir, series)  # 거래대금 상위 선별용 랭킹 저장
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
    data_dir: str | Path = DEFAULT_DATA_DIR, *, years: int = DEFAULT_LOAD_YEARS,
    with_flow: bool = True, on_progress=None,
) -> dict[str, Any]:
    """버튼 하나로 한국시장 전체(KOSPI+KOSDAQ) 과거 데이터를 일괄 적재(덮어쓰기).

    - 가격(OHLCV): 날짜별 단면으로 전 종목을 효율 적재. 기존 데이터가 있어도 덮어쓴다(merge=False).
    - 수급(투자자별): KRX 로그인이 설정된 경우에만 종목별로 best-effort 적재(종목 수만큼 호출 → 느림).
      로그인 없거나 일부 실패해도 전체 작업은 계속한다.
    on_progress(msg)로 진행 상황을 보고한다(대시보드 작업 로그).
    """
    from datetime import timedelta

    from etl.flow import ingest_flow
    from orchestrator.config import apply_krx_credentials

    def progress(msg):
        if on_progress:
            on_progress(msg)

    end = date.today()
    start = end - timedelta(days=365 * years)
    progress(f"전체시장 적재 시작 (최근 {years}년)")

    price = ingest_universe(start, end, market="ALL", data_dir=data_dir, merge=False,
                            on_progress=on_progress, write_rank=True)

    flow_ok = flow_fail = 0
    flow_enabled = with_flow and apply_krx_credentials()
    if flow_enabled:
        tickers = list_universe("ALL", end)
        progress(f"수급 적재 시작: {len(tickers)}종목 (종목당 1회 호출 → 시간 소요)")
        for i, tkr in enumerate(tickers, 1):
            try:
                ingest_flow(tkr, start, end, data_dir)
                flow_ok += 1
            except Exception:  # 개별 종목 실패(데이터 없음 등)는 건너뛰고 계속
                flow_fail += 1
            if i % 50 == 0 or i == len(tickers):
                progress(f"수급 {i}/{len(tickers)} (성공 {flow_ok}, 실패 {flow_fail})")
    else:
        progress("수급 건너뜀 (KRX 로그인 없음 — ③ 설정에서 키 입력 시 가능)"
                 if with_flow else "수급 비활성")

    progress(f"완료: OHLCV {price['ingested']}종목, 수급 {flow_ok}종목")
    return {
        "years": years,
        "price_tickers": price["ingested"],
        "trading_days": price["trading_days"],
        "flow_enabled": flow_enabled,
        "flow_ok": flow_ok,
        "flow_fail": flow_fail,
    }


def update_universe(market: str = "KOSPI200", data_dir: str | Path = DEFAULT_DATA_DIR,
                    days: int = 5) -> dict[str, Any]:
    """최근 days일을 받아 증분 병합 (스케줄러 일일 갱신용). 멱등."""
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
