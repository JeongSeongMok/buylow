"""원본 시세 → LEAN 데이터 포맷 변환/기록 (순수 로직, 단위 테스트 가능).

LEAN equity 일봉 규칙: `equity/<market>/daily/<ticker>.zip` 안에 `<ticker>.csv`,
라인 `YYYYMMDD 00:00,O,H,L,C,V`, **가격 ×10000 정수**(읽을 때 /10000).
"""

from __future__ import annotations

import csv
import io
import zipfile
from pathlib import Path

from .sources import Bar

PRICE_SCALE = 10_000  # LEAN equity 일봉 저장 스케일


def equity_daily_zip_path(data_dir: str | Path, market: str, ticker: str) -> Path:
    return Path(data_dir) / "equity" / market / "daily" / f"{ticker}.zip"


def write_equity_daily(data_dir: str | Path, market: str, ticker: str, bars: list[Bar]) -> Path:
    """일봉 리스트를 LEAN 포맷 zip으로 기록하고 경로 반환.

    KRX 가격은 보통 수정주가라 별도 보정 불필요 → factor_files를 비워둔다(이중 보정 방지).
    """
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
