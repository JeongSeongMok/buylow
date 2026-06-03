"""원본 시세 → LEAN 데이터 포맷 변환/기록 (순수 로직, 단위 테스트 가능).

LEAN equity 일봉 규칙: `equity/<market>/daily/<ticker>.zip` 안에 `<ticker>.csv`,
라인 `YYYYMMDD 00:00,O,H,L,C,V`, **가격 ×10000 정수**(읽을 때 /10000).
"""

from __future__ import annotations

import csv
import io
import zipfile
from datetime import date, datetime
from pathlib import Path

from .sources import Bar, MinuteBar

PRICE_SCALE = 10_000  # LEAN equity 가격 저장 스케일(일봉/분봉 공통)


def equity_daily_zip_path(data_dir: str | Path, market: str, ticker: str) -> Path:
    return Path(data_dir) / "equity" / market / "daily" / f"{ticker}.zip"


def read_equity_daily(data_dir: str | Path, market: str, ticker: str) -> list[Bar]:
    """저장된 LEAN 일봉을 Bar 리스트로 되읽기(가격 역스케일). 증분 병합/조회에 사용."""
    zp = equity_daily_zip_path(data_dir, market, ticker)
    if not zp.exists():
        return []
    with zipfile.ZipFile(zp) as zf:
        text = zf.read(f"{ticker}.csv").decode("utf-8")
    bars = []
    for line in text.strip().splitlines():
        dt, o, h, l, c, v = line.split(",")
        d = datetime.strptime(dt.split()[0], "%Y%m%d").date()
        bars.append(Bar(d, int(o) / PRICE_SCALE, int(h) / PRICE_SCALE,
                        int(l) / PRICE_SCALE, int(c) / PRICE_SCALE, int(v)))
    return bars


def write_equity_daily(data_dir: str | Path, market: str, ticker: str,
                       bars: list[Bar], merge: bool = False) -> Path:
    """일봉 리스트를 LEAN 포맷 zip으로 기록하고 경로 반환.

    merge=True면 기존 파일의 봉과 날짜 기준으로 병합(새 값이 우선) → 증분 적재.
    KRX 가격은 보통 수정주가라 별도 보정 불필요 → factor_files를 비워둔다(이중 보정 방지).
    """
    if merge:
        by_day = {b.day: b for b in read_equity_daily(data_dir, market, ticker)}
        for b in bars:
            by_day[b.day] = b
        bars = list(by_day.values())

    buf = io.StringIO()
    writer = csv.writer(buf)
    for b in sorted(bars, key=lambda x: x.day):
        writer.writerow([
            f"{b.day:%Y%m%d} 00:00",
            int(round(b.open * PRICE_SCALE)),
            int(round(b.high * PRICE_SCALE)),
            int(round(b.low * PRICE_SCALE)),
            int(round(b.close * PRICE_SCALE)),
            b.volume,
        ])

    out = equity_daily_zip_path(data_dir, market, ticker)
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"{ticker}.csv", buf.getvalue())

    # map/factor files 디렉토리(빈 폴더라도 있어야 LocalDisk*Provider가 에러 안 냄)
    for sub in ("map_files", "factor_files"):
        (Path(data_dir) / "equity" / market / sub).mkdir(parents=True, exist_ok=True)
    return out


# ── 분봉(minute) ─────────────────────────────────────────────────────────────
# LEAN equity 분봉 규칙: `equity/<market>/minute/<ticker>/<yyyymmdd>_trade.zip` 안에
# `<yyyymmdd>_<ticker>_minute_trade.csv`, 라인 `<자정기준 ms>,O,H,L,C,V` (가격 ×10000).
# ticker는 소문자 관례(LeanData) — KRX 코드는 숫자라 영향 없지만 일관성 위해 소문자.

def equity_minute_zip_path(data_dir: str | Path, market: str, ticker: str, day: date) -> Path:
    t = ticker.lower()
    return (Path(data_dir) / "equity" / market / "minute" / t / f"{day:%Y%m%d}_trade.zip")


def list_minute_days(data_dir: str | Path, market: str, ticker: str) -> set[date]:
    """디스크에 분봉이 적재된 날짜 집합. 백테스트가 (종목,일)별로 장중타이밍/시가폴백을 고르는 근거."""
    d = Path(data_dir) / "equity" / market / "minute" / ticker.lower()
    if not d.is_dir():
        return set()
    out: set[date] = set()
    for p in d.glob("*_trade.zip"):
        s = p.name.split("_")[0]
        if len(s) == 8 and s.isdigit():
            out.add(date(int(s[:4]), int(s[4:6]), int(s[6:8])))
    return out


def read_equity_minute(data_dir: str | Path, market: str, ticker: str,
                       day: date) -> list[MinuteBar]:
    """저장된 LEAN 분봉(하루치)을 MinuteBar 리스트로 되읽기(가격 역스케일)."""
    zp = equity_minute_zip_path(data_dir, market, ticker, day)
    if not zp.exists():
        return []
    entry = f"{day:%Y%m%d}_{ticker.lower()}_minute_trade.csv"
    with zipfile.ZipFile(zp) as zf:
        text = zf.read(entry).decode("utf-8")
    bars = []
    for line in text.strip().splitlines():
        ms, o, h, l, c, v = line.split(",")
        bars.append(MinuteBar(int(ms), int(o) / PRICE_SCALE, int(h) / PRICE_SCALE,
                              int(l) / PRICE_SCALE, int(c) / PRICE_SCALE, int(v)))
    return bars


def write_equity_minute(data_dir: str | Path, market: str, ticker: str,
                        day: date, bars: list[MinuteBar]) -> Path:
    """하루치 분봉을 LEAN 포맷 zip으로 기록(시간 오름차순, 가격 ×10000). 경로 반환."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    for b in sorted(bars, key=lambda x: x.ms):
        writer.writerow([
            int(b.ms),
            int(round(b.open * PRICE_SCALE)),
            int(round(b.high * PRICE_SCALE)),
            int(round(b.low * PRICE_SCALE)),
            int(round(b.close * PRICE_SCALE)),
            b.volume,
        ])
    out = equity_minute_zip_path(data_dir, market, ticker, day)
    out.parent.mkdir(parents=True, exist_ok=True)
    entry = f"{day:%Y%m%d}_{ticker.lower()}_minute_trade.csv"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(entry, buf.getvalue())
    for sub in ("map_files", "factor_files"):
        (Path(data_dir) / "equity" / market / sub).mkdir(parents=True, exist_ok=True)
    return out
