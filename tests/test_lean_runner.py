"""LeanRunner의 순수 로직 단위 테스트 (LEAN/.NET 없이 빠르게)."""

import os

import pytest

from orchestrator.lean.runner import RunRequest, RunResult, _build_config, _STAT_RE


def test_request_defaults_algorithm_type_to_filename_stem():
    req = RunRequest(strategy_path="strategies/SmokeTestAlgorithm.py", data_folder="/tmp/data")
    assert req.resolved_algorithm_type() == "SmokeTestAlgorithm"


def test_request_explicit_algorithm_type_wins():
    req = RunRequest(strategy_path="strategies/x.py", data_folder="/tmp", algorithm_type="Custom")
    assert req.resolved_algorithm_type() == "Custom"


def test_build_config_core_fields(tmp_path):
    req = RunRequest(
        strategy_path="strategies/SmokeTestAlgorithm.py",
        data_folder=str(tmp_path),
        parameters={"threshold": 0.12, "n": 5},  # 비문자열도 받아 문자열로 강제돼야 함
    )
    cfg = _build_config(req, results_dir=tmp_path / "run1", algorithm_id="run1")

    assert cfg["environment"] == "backtesting"
    assert cfg["algorithm-language"] == "Python"
    assert cfg["algorithm-type-name"] == "SmokeTestAlgorithm"
    assert cfg["algorithm-id"] == "run1"
    assert os.path.isabs(cfg["algorithm-location"])  # 절대경로로 해석
    assert cfg["results-destination-folder"] == str(tmp_path / "run1")
    # 파라미터는 전부 문자열이어야 함 (LEAN get_parameter는 문자열 반환)
    assert cfg["parameters"] == {"threshold": "0.12", "n": "5"}
    # 백테스트 환경 핸들러가 있어야 함
    assert "backtesting" in cfg["environments"]
    assert cfg["environments"]["backtesting"]["live-mode"] is False


@pytest.mark.parametrize(
    "line,name,value",
    [
        ("STATISTICS:: Total Orders 1", "Total Orders", "1"),
        ("STATISTICS:: Net Profit 1.694%", "Net Profit", "1.694%"),
        ("STATISTICS:: Total Fees $3.45", "Total Fees", "$3.45"),
    ],
)
def test_stat_regex_parses_name_and_value(line, name, value):
    m = _STAT_RE.search(line)
    assert m is not None
    assert m.group(1).strip() == name
    assert m.group(2).strip() == value


def test_run_result_success_only_on_zero_exit(tmp_path):
    ok = RunResult("r", 0, {}, tmp_path, tmp_path / "run.log", None)
    bad = RunResult("r", 1, {}, tmp_path, tmp_path / "run.log", None)
    assert ok.success is True
    assert bad.success is False


@pytest.mark.integration
def test_backtest_end_to_end():
    """실제 LEAN 백테스트 (LEAN_DATA_DIR + .NET + Python3.11 필요). 기본 실행에서 제외."""
    data_dir = os.environ.get("LEAN_DATA_DIR")
    if not data_dir:
        pytest.skip("LEAN_DATA_DIR 미설정")
    from orchestrator.lean import LeanRunner
    result = LeanRunner().run_backtest(
        RunRequest(strategy_path="strategies/SmokeTestAlgorithm.py", data_folder=data_dir)
    )
    assert result.success
    assert "Total Orders" in result.statistics
