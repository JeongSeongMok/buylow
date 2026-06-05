"""백테스트/실행 이력을 보존하는 SQLite 저장소.

영속성 결정(docs/ARCHITECTURE.md): 별도 DB 서버 없이 SQLite(파이썬 내장) + 디스크 파일.
- 구조화 상태(실행 메타·통계·파라미터)는 여기 SQLite에.
- 큰 blob(결과 JSON·로그)은 runs/<id>/ 파일로 두고, 여기엔 경로만 저장.
앱을 껐다 켜도 이전 실행 이력이 보이도록 하는 게 목적.

설계 메모: 동시성은 매우 낮으므로(개인용 로컬) 연산마다 새 커넥션을 열고 닫는다.
FastAPI 스레드풀에서 호출돼도 안전하게. WAL 모드로 동시 읽기를 허용한다.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]

# dict로 직렬화/역직렬화할 JSON 컬럼
_JSON_COLUMNS = ("parameters", "statistics")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id          TEXT PRIMARY KEY,
    strategy        TEXT NOT NULL,
    algorithm_type  TEXT NOT NULL,
    data_folder     TEXT NOT NULL,
    parameters      TEXT NOT NULL,   -- json
    exit_code       INTEGER NOT NULL,
    success         INTEGER NOT NULL, -- 0/1
    statistics      TEXT NOT NULL,   -- json
    run_dir         TEXT,
    log_path        TEXT,
    result_json     TEXT,
    created_at      TEXT NOT NULL    -- ISO8601
);
"""


def default_db_path() -> Path:
    """기본 DB 위치 (repo 루트의 buylow.db; gitignore됨)."""
    return REPO_ROOT / "buylow.db"


class RunStore:
    """실행(run) 이력 저장소."""

    def __init__(self, db_path: str | Path | None = None):
        self.db_path = str(db_path or default_db_path())
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def save_run(self, record: dict[str, Any]) -> dict[str, Any]:
        """실행 1건 저장. record는 컬럼 키를 가진 dict (parameters/statistics는 dict, success는 bool).

        영속성 계층을 lean 타입과 분리하기 위해 일부러 plain dict를 받는다.
        """
        row = self._to_row(record)
        with self._connect() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO runs
                   (run_id, strategy, algorithm_type, data_folder, parameters,
                    exit_code, success, statistics, run_dir, log_path, result_json, created_at)
                   VALUES (:run_id, :strategy, :algorithm_type, :data_folder, :parameters,
                           :exit_code, :success, :statistics, :run_dir, :log_path, :result_json, :created_at)""",
                row,
            )
        return self.get_run(row["run_id"])  # type: ignore[return-value]

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            cur = conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,))
            r = cur.fetchone()
        return self._from_row(r) if r else None

    def list_runs(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as conn:
            cur = conn.execute(
                "SELECT * FROM runs ORDER BY created_at DESC LIMIT ?", (limit,)
            )
            rows = cur.fetchall()
        return [self._from_row(r) for r in rows]

    def delete_run(self, run_id: str) -> bool:
        """실행 1건 삭제(DB 행만). 디스크 blob(runs/<id>/) 삭제는 호출측 책임.
        삭제된 행이 있으면 True."""
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM runs WHERE run_id = ?", (run_id,))
        return cur.rowcount > 0

    def clear_runs(self) -> int:
        """모든 실행 이력 삭제(DB 행). 삭제 건수 반환. 디스크 blob 삭제는 호출측 책임."""
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM runs")
        return cur.rowcount

    # --- (역)직렬화 ---
    def _to_row(self, record: dict[str, Any]) -> dict[str, Any]:
        row = dict(record)
        row.setdefault("created_at", datetime.now().isoformat(timespec="seconds"))
        row["success"] = 1 if record.get("success") else 0
        for col in _JSON_COLUMNS:
            row[col] = json.dumps(record.get(col) or {}, ensure_ascii=False)
        # 누락 가능한 옵션 컬럼 기본값
        for col in ("run_dir", "log_path", "result_json"):
            row.setdefault(col, None)
        return row

    def _from_row(self, row: sqlite3.Row) -> dict[str, Any]:
        d = dict(row)
        d["success"] = bool(d["success"])
        for col in _JSON_COLUMNS:
            d[col] = json.loads(d[col])
        return d
