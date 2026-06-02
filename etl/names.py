"""KRX 종목코드 → 종목명 매핑 적재/조회.

전 종목 이름을 FinanceDataReader로 한 번에 받아 data/krx/names.csv (라인: `코드,이름`)로 저장.
대시보드가 백테스트 결과/데이터 화면에서 코드 대신/함께 이름을 보여주는 데 쓴다.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA_DIR = REPO_ROOT / "data"


def names_csv_path(data_dir: str | Path) -> Path:
    return Path(data_dir) / "krx" / "names.csv"


def fetch_and_save_names(data_dir: str | Path = DEFAULT_DATA_DIR) -> int:
    """전 종목 코드→이름을 받아 저장(무인증, 1회 호출). 반환: 저장된 종목 수."""
    import FinanceDataReader as fdr
    df = fdr.StockListing("KRX")
    cols = list(df.columns)
    code_col = "Code" if "Code" in cols else ("Symbol" if "Symbol" in cols else cols[0])
    name_col = "Name" if "Name" in cols else cols[1]
    lines = []
    for _, r in df.iterrows():
        code = str(r[code_col]).strip()
        name = str(r[name_col]).strip().replace(",", " ")  # csv 안전
        if code and name and code.lower() != "nan":
            lines.append(f"{code},{name}")
    out = names_csv_path(data_dir)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return len(lines)


def load_names(data_dir: str | Path = DEFAULT_DATA_DIR) -> dict[str, str]:
    """코드→이름 딕셔너리. 파일 없으면 빈 dict(대시보드는 코드만 표시)."""
    fp = names_csv_path(data_dir)
    if not fp.exists():
        return {}
    out: dict[str, str] = {}
    for line in fp.read_text(encoding="utf-8").strip().splitlines():
        parts = line.split(",", 1)
        if len(parts) == 2:
            out[parts[0].strip()] = parts[1].strip()
    return out
