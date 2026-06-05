"""RunStore(SQLite) 단위 테스트 — 임시 DB 파일 사용."""

import pytest

from orchestrator.persistence import RunStore


@pytest.fixture
def store(tmp_path):
    return RunStore(tmp_path / "test.db")


def _record(run_id="r1", success=True):
    return {
        "run_id": run_id,
        "strategy": "strategies/SmokeTestAlgorithm.py",
        "algorithm_type": "SmokeTestAlgorithm",
        "data_folder": "/data",
        "parameters": {"threshold": "0.12"},
        "exit_code": 0 if success else 1,
        "success": success,
        "statistics": {"Net Profit": "1.694%"},
        "run_dir": "/runs/r1",
        "log_path": "/runs/r1/run.log",
        "result_json": "/runs/r1/r1-summary.json",
    }


def test_save_and_get_roundtrip(store):
    saved = store.save_run(_record())
    got = store.get_run("r1")
    assert got is not None
    # dict 컬럼은 dict로, success는 bool로 복원돼야 함
    assert got["parameters"] == {"threshold": "0.12"}
    assert got["statistics"] == {"Net Profit": "1.694%"}
    assert got["success"] is True
    assert "created_at" in got and got["created_at"]  # 자동 채워짐
    assert saved["run_id"] == "r1"


def test_get_missing_returns_none(store):
    assert store.get_run("nope") is None


def test_list_runs_returns_all(store):
    store.save_run(_record("r1"))
    store.save_run(_record("r2", success=False))
    runs = store.list_runs()
    assert {r["run_id"] for r in runs} == {"r1", "r2"}


def test_persists_across_instances(tmp_path):
    path = tmp_path / "persist.db"
    RunStore(path).save_run(_record("r1"))
    # 새 인스턴스(=앱 재시작 시뮬)에서도 보여야 함
    assert RunStore(path).get_run("r1") is not None


def test_delete_run(store):
    store.save_run(_record("r1"))
    store.save_run(_record("r2"))
    assert store.delete_run("r1") is True
    assert store.get_run("r1") is None
    assert store.get_run("r2") is not None  # 다른 건 남음
    assert store.delete_run("nope") is False  # 없는 건 False


def test_clear_runs(store):
    store.save_run(_record("r1"))
    store.save_run(_record("r2"))
    assert store.clear_runs() == 2
    assert store.list_runs() == []
