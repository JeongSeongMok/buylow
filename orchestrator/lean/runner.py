"""LEAN 백테스트를 프로그램으로 실행하는 Runner — 오케스트레이터의 첫 벽돌.

config.json 생성 → LEAN 프로세스 spawn → 결과(통계·산출물) 수집/파싱까지, '1프로세스=1작업'
모델(docs/ARCHITECTURE.md)을 코드로 구현한다. run-backtest.sh의 셸 흐름을 흡수한 것.

지금은 백테스트만 지원한다. 라이브(env=live-toss)는 토스 어댑터 단계에서 확장한다.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from ..config import get_risk_config
from .environment import REPO_ROOT, LeanEnvironment, prepare_environment

RUNS_DIR = REPO_ROOT / "runs"

# LEAN이 stdout에 찍는 "STATISTICS:: <name> <value>" 라인. 값은 마지막 토큰.
_STAT_RE = re.compile(r"STATISTICS:: (.+?)\s+(\S+)\s*$")


@dataclass
class RunRequest:
    """백테스트 1건의 요청."""

    strategy_path: str                              # 전략 .py 경로 (repo 상대/절대)
    data_folder: str                                # LEAN 포맷 데이터 루트
    algorithm_type: str | None = None               # 클래스명 (None이면 파일명 stem)
    parameters: dict[str, str] = field(default_factory=dict)  # 전략 파라미터 (get_parameter)

    def resolved_strategy(self) -> Path:
        return Path(self.strategy_path).resolve()

    def resolved_algorithm_type(self) -> str:
        return self.algorithm_type or self.resolved_strategy().stem


@dataclass
class RunResult:
    """백테스트 1건의 결과."""

    run_id: str
    exit_code: int
    statistics: dict[str, str]
    run_dir: Path
    log_path: Path
    result_json: Path | None

    @property
    def success(self) -> bool:
        # LEAN thin 런처는 정상 완주(Completed) 시에만 0을 반환한다 (Program.cs Exit 로직).
        return self.exit_code == 0


def _params_with_risk(parameters: dict) -> dict:
    """전략 파라미터에 전역 리스크 설정(%)을 risk_* 키로 합쳐 LEAN에 전달."""
    params = {k: str(v) for k, v in parameters.items()}
    risk = get_risk_config()
    for k, v in risk.items():
        if v is not None:
            params[f"risk_{k}"] = str(v)
    return params


def _build_config(request: RunRequest, results_dir: Path, algorithm_id: str) -> dict:
    """백테스트용 LEAN config(dict)를 생성. launcher/config.json의 백테스트 키를 코드로 구성.

    템플릿(JSON5, 주석 포함)을 sed로 치환하는 대신 dict로 만들어, 파라미터/환경을 동적으로
    주입할 수 있게 한다. 핸들러 구성은 검증된 백테스트 설정과 동일하다.
    """
    return {
        "environment": "backtesting",
        "algorithm-id": algorithm_id,
        "algorithm-type-name": request.resolved_algorithm_type(),
        "algorithm-language": "Python",
        "algorithm-location": str(request.resolved_strategy()),
        "data-folder": str(Path(request.data_folder).resolve()),
        # 결과 파일(<id>.json, <id>-summary.json)을 이 run 디렉토리에 쓰게 한다
        "results-destination-folder": str(results_dir),
        # 핸들러 — Composer가 이름으로 로드 (출력폴더에 어셈블리 존재해야 함)
        "log-handler": "QuantConnect.Logging.CompositeLogHandler",
        "messaging-handler": "QuantConnect.Messaging.Messaging",
        "job-queue-handler": "QuantConnect.Queues.JobQueue",
        "api-handler": "QuantConnect.Api.Api",
        "map-file-provider": "QuantConnect.Data.Auxiliary.LocalDiskMapFileProvider",
        "factor-file-provider": "QuantConnect.Data.Auxiliary.LocalDiskFactorFileProvider",
        "data-provider": "QuantConnect.Lean.Engine.DataFeeds.DefaultDataProvider",
        "data-channel-provider": "DataChannelProvider",
        "object-store": "QuantConnect.Lean.Engine.Storage.LocalObjectStore",
        "data-aggregator": "QuantConnect.Lean.Engine.DataFeeds.AggregationManager",
        "job-user-id": "0",
        "api-access-token": "",
        "job-organization-id": "",
        "symbol-minute-limit": 10000,
        "symbol-second-limit": 10000,
        "symbol-tick-limit": 10000,
        "maximum-data-points-per-chart-series": 1000000,
        "maximum-chart-series": 30,
        "force-exchange-always-open": False,
        # 전략 파라미터(전부 문자열) + 전역 리스크 설정 주입(전략은 get_parameter로 읽음).
        "parameters": _params_with_risk(request.parameters),
        # PYTHONPATH로 주입하므로 비워둠
        "python-additional-paths": [],
        "environments": {
            "backtesting": {
                "live-mode": False,
                "setup-handler": "QuantConnect.Lean.Engine.Setup.BacktestingSetupHandler",
                "result-handler": "QuantConnect.Lean.Engine.Results.BacktestingResultHandler",
                "data-feed-handler": "QuantConnect.Lean.Engine.DataFeeds.FileSystemDataFeed",
                "real-time-handler": "QuantConnect.Lean.Engine.RealTime.BacktestingRealTimeHandler",
                "history-provider": [
                    "QuantConnect.Lean.Engine.HistoricalData.SubscriptionDataReaderHistoryProvider"
                ],
                "transaction-handler": "QuantConnect.Lean.Engine.TransactionHandlers.BacktestingTransactionHandler",
            }
        },
    }


class LeanRunner:
    """LEAN 프로세스를 띄워 백테스트를 실행한다."""

    def __init__(self, env: LeanEnvironment | None = None):
        # env 준비(=런처 빌드 포함)는 비용이 있으므로 한 번 만들어 재사용한다.
        self._env = env or prepare_environment()

    def run_backtest(self, request: RunRequest, on_start=None) -> RunResult:
        """백테스트 실행. on_start(run_id, log_path)가 주어지면 spawn 직전에 호출(진행 추적용)."""
        strategy = request.resolved_strategy()
        if not strategy.is_file():
            raise FileNotFoundError(f"전략 파일 없음: {strategy}")
        data_folder = Path(request.data_folder)
        if not data_folder.is_dir():
            raise FileNotFoundError(f"데이터 폴더 없음: {data_folder}")

        algo_type = request.resolved_algorithm_type()
        run_id = f"{algo_type}-{datetime.now():%Y%m%d-%H%M%S}"
        run_dir = RUNS_DIR / run_id
        run_dir.mkdir(parents=True, exist_ok=True)

        # 전략 import + 'from AlgorithmImports import *' 해소를 위한 PYTHONPATH.
        # REPO_ROOT를 넣어 전략이 공용 라이브러리(예: market.krx)를 import할 수 있게 한다.
        pythonpath_parts = [
            str(self._env.venv_site_packages),
            str(self._env.algorithm_imports_dir),
            str(REPO_ROOT),
            str(strategy.parent),
        ]
        proc_env = self._env.process_env(pythonpath_parts)

        # config는 LEAN이 cwd에서 읽으므로 런처 출력폴더에 쓴다. 결과물은 run_dir로 분리.
        out_dir = self._env.launcher_dll.parent
        config = _build_config(request, run_dir, run_id)
        (out_dir / "config.json").write_text(json.dumps(config, indent=2), encoding="utf-8")

        log_path = run_dir / "run.log"
        if on_start:
            on_start(run_id, log_path)
        statistics: dict[str, str] = {}
        # buffering=1(라인 버퍼) → 실행 중에도 로그가 즉시 파일에 기록돼 대시보드에서 실시간 확인 가능
        with open(log_path, "w", encoding="utf-8", buffering=1) as log:
            proc = subprocess.Popen(
                [str(self._env.dotnet_exe), "BuylowLauncher.dll"],
                cwd=str(out_dir),
                env=proc_env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            assert proc.stdout is not None
            for line in proc.stdout:
                log.write(line)
                m = _STAT_RE.search(line)
                if m:
                    statistics[m.group(1).strip()] = m.group(2).strip()
            proc.wait()

        # 결과 요약 JSON 위치 (algorithm-id 기반). 못 찾으면 None.
        result_json = next(iter(run_dir.glob("*-summary.json")), None) \
            or next(iter(run_dir.glob(f"{run_id}.json")), None)

        return RunResult(
            run_id=run_id,
            exit_code=proc.returncode,
            statistics=statistics,
            run_dir=run_dir,
            log_path=log_path,
            result_json=result_json,
        )


def _parse_param(item: str) -> tuple[str, str]:
    if "=" not in item:
        raise argparse.ArgumentTypeError(f"--param 형식은 key=value 여야 함: {item}")
    k, v = item.split("=", 1)
    return k, v


def main() -> int:
    """CLI: run-backtest.sh의 프로그램 버전. 결과 통계를 출력하고 종료코드로 성공 여부 전달."""
    parser = argparse.ArgumentParser(
        prog="python -m orchestrator.lean",
        description="LEAN 백테스트 실행 (오케스트레이터 Runner)",
    )
    parser.add_argument("--strategy", default="strategies/SmokeTestAlgorithm.py")
    parser.add_argument("--algo-type", default=None, help="클래스명 (기본: 파일명)")
    parser.add_argument(
        "--data-folder", default=os.environ.get("LEAN_DATA_DIR"),
        help="LEAN 포맷 데이터 루트 (또는 LEAN_DATA_DIR 환경변수)",
    )
    parser.add_argument("--param", action="append", default=[], type=_parse_param,
                        help="전략 파라미터 key=value (반복 가능)")
    args = parser.parse_args()

    if not args.data_folder:
        parser.error("데이터 폴더를 --data-folder 또는 LEAN_DATA_DIR로 지정하세요")

    request = RunRequest(
        strategy_path=args.strategy,
        data_folder=args.data_folder,
        algorithm_type=args.algo_type,
        parameters=dict(args.param),
    )
    result = LeanRunner().run_backtest(request)

    print(f"\n=== run {result.run_id} (exit={result.exit_code}, success={result.success}) ===")
    for key in ("Total Orders", "Net Profit", "Sharpe Ratio", "Total Fees", "Drawdown"):
        if key in result.statistics:
            print(f"  {key}: {result.statistics[key]}")
    print(f"  results: {result.result_json or '(요약 JSON 없음)'}")
    print(f"  log: {result.log_path}")
    return 0 if result.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
