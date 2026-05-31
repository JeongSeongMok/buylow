"""유니버스 대량 ETL 테스트 — pykrx 결합이라 통합 테스트(로그인 필요, 기본 제외)."""

from datetime import date

import pytest


@pytest.mark.integration
def test_ingest_kospi200_small(tmp_path):
    from orchestrator.config import apply_krx_credentials
    if not apply_krx_credentials():
        pytest.skip("KRX 크리덴셜 미설정")
    from etl.universe import ingest_universe

    info = ingest_universe(date(2024, 1, 2), date(2024, 1, 5), "KOSPI200", tmp_path)
    assert info["universe"] >= 100
    assert info["ingested"] >= 100  # 대부분 적재
    # 삼성전자는 KOSPI200 구성종목 → 파일 존재
    assert (tmp_path / "equity" / "krx" / "daily" / "005930.zip").exists()
