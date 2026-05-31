"""전략(프레임워크) 테스트.

Alpha 모델/프레임워크는 LEAN 런타임에 결합돼 있어 단위 테스트가 어렵다 → end-to-end 통합
테스트로 검증한다(기본 실행 제외). 실데이터 ETL + 멀티알파 백테스트 완주를 확인.
"""

from datetime import date

import pytest


@pytest.mark.integration
def test_krx_framework_multialpha_backtest(tmp_path):
    from etl import krx as etl_krx
    from etl.sources import get_source
    from orchestrator.lean import LeanRunner, RunRequest

    data_dir = tmp_path / "data"
    # 예시 전략이 쓰는 종목/기간(005930, 2023) 적재
    etl_krx.ingest("005930", date(2023, 1, 1), date(2023, 12, 31), data_dir, get_source("pykrx"))

    result = LeanRunner().run_backtest(RunRequest(
        strategy_path="strategies/KrxFrameworkExample.py",
        data_folder=str(data_dir),
    ))
    assert result.success
    # 두 Alpha가 결합돼 매매가 발생해야 함
    assert int(result.statistics.get("Total Orders", "0")) >= 1
