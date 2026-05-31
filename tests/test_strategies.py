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


@pytest.mark.integration
def test_flow_alpha_backtest(tmp_path):
    """수급 커스텀 데이터 + 수급 alpha end-to-end (KRX 로그인 필요)."""
    import json
    from orchestrator.config import apply_krx_credentials
    if not apply_krx_credentials():
        pytest.skip("KRX 크리덴셜 미설정")
    from etl import flow as etl_flow, krx as etl_krx
    from etl.sources import get_source
    from orchestrator.lean import LeanRunner, RunRequest

    dd = tmp_path / "data"
    etl_krx.ingest("005930", date(2023, 1, 1), date(2023, 12, 31), dd, get_source("pykrx"))
    etl_flow.ingest_flow("005930", date(2023, 1, 1), date(2023, 12, 31), dd)

    spec = {"alphas": [{"name": "flow", "params": {"lookback": 5}}],
            "universe": ["005930"], "start": "2023-02-01", "end": "2023-12-28", "cash": 10000000}
    result = LeanRunner().run_backtest(RunRequest(
        strategy_path="strategies/Composed.py", data_folder=str(dd),
        algorithm_type="Composed", parameters={"composition": json.dumps(spec)}))
    assert result.success
    assert int(result.statistics.get("Total Orders", "0")) >= 1


@pytest.mark.integration
def test_value_alpha_backtest(tmp_path):
    """저PBR 가치 커스텀 데이터 + alpha end-to-end (KRX 로그인 필요)."""
    import json
    from orchestrator.config import apply_krx_credentials
    if not apply_krx_credentials():
        pytest.skip("KRX 크리덴셜 미설정")
    from etl import fundamental as etl_fund, krx as etl_krx
    from etl.sources import get_source
    from orchestrator.lean import LeanRunner, RunRequest

    dd = tmp_path / "data"
    etl_krx.ingest("005930", date(2023, 1, 1), date(2023, 12, 31), dd, get_source("pykrx"))
    etl_fund.ingest_fundamental("005930", date(2023, 1, 1), date(2023, 12, 31), dd)

    spec = {"alphas": [{"name": "value", "params": {"max_pbr": 2.0, "period_days": 20}}],
            "universe": ["005930"], "start": "2023-02-01", "end": "2023-12-28", "cash": 10000000}
    result = LeanRunner().run_backtest(RunRequest(
        strategy_path="strategies/Composed.py", data_folder=str(dd),
        algorithm_type="Composed", parameters={"composition": json.dumps(spec)}))
    assert result.success
    assert int(result.statistics.get("Total Orders", "0")) >= 1
