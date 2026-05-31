"""수급 ETL 테스트 — 쓰기 포맷은 단위로, 실 조회는 통합(로그인 필요)으로."""

from datetime import date

import pytest

from etl.flow import FlowRecord, flow_csv_path, write_flow


def test_write_flow_format(tmp_path):
    records = [
        FlowRecord(date(2024, 1, 3), 30127750000, -302812522300, 278867901900),
        FlowRecord(date(2024, 1, 2), 182974012300, 45093469200, -225954307900),
    ]
    write_flow(tmp_path, "005930", records)
    lines = flow_csv_path(tmp_path, "005930").read_text().strip().splitlines()
    # 날짜 오름차순 정렬
    assert lines[0].startswith("20240102")
    assert lines[1].startswith("20240103")
    # foreign,institution,individual 순서
    f, i, p = lines[0].split(",")[1:]
    assert (int(f), int(i), int(p)) == (182974012300, 45093469200, -225954307900)


@pytest.mark.integration
def test_fetch_flow_real(tmp_path):
    """실제 pykrx 수급 조회 — KRX 로그인 필요. 크리덴셜 없으면 skip."""
    from orchestrator.config import apply_krx_credentials
    if not apply_krx_credentials():
        pytest.skip("KRX 크리덴셜 미설정")
    from etl.flow import ingest_flow
    info = ingest_flow("005930", date(2024, 1, 2), date(2024, 1, 10), tmp_path)
    assert info["rows"] >= 3
    assert flow_csv_path(tmp_path, "005930").exists()
