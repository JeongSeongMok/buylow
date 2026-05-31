"""펀더멘털 ETL 테스트 — 쓰기 포맷 단위 + 실 조회 통합(로그인 필요)."""

from datetime import date

import pytest

from etl.fundamental import FundamentalRecord, fundamental_csv_path, write_fundamental


def test_write_fundamental_format(tmp_path):
    write_fundamental(tmp_path, "005930", [
        FundamentalRecord(date(2023, 1, 3), 9.59, 1.27, 2.61),
        FundamentalRecord(date(2023, 1, 2), 9.61, 1.27, 2.60),
    ])
    lines = fundamental_csv_path(tmp_path, "005930").read_text().strip().splitlines()
    assert lines[0].startswith("20230102")  # 날짜 오름차순
    per, pbr, div = lines[0].split(",")[1:]
    assert float(per) == 9.61 and float(pbr) == 1.27


@pytest.mark.integration
def test_fetch_fundamental_real(tmp_path):
    from orchestrator.config import apply_krx_credentials
    if not apply_krx_credentials():
        pytest.skip("KRX 크리덴셜 미설정")
    from etl.fundamental import ingest_fundamental
    info = ingest_fundamental("005930", date(2024, 1, 2), date(2024, 1, 10), tmp_path)
    assert info["rows"] >= 3
    assert fundamental_csv_path(tmp_path, "005930").exists()
