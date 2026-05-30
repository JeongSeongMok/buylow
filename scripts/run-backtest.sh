#!/usr/bin/env bash
#
# run-backtest.sh — buylow LEAN 연동 백테스트 실행/검증 스크립트.
#
# 무수정 LEAN 엔진(NuGet) + 우리 thin 런처로 Python 전략 백테스트를 끝까지 돌린다.
# 토스/실거래 없이 "LEAN 연동이 살아있는지"를 한 방에 확인하는 용도(스모크 테스트 포함).
#
# 사용법:
#   scripts/run-backtest.sh                         # 기본: SmokeTestAlgorithm
#   STRATEGY=strategies/My.py ALGO_TYPE=My \        # 다른 전략 지정
#     scripts/run-backtest.sh
#
# 환경변수(override 가능):
#   STRATEGY      실행할 .py 경로 (default: strategies/SmokeTestAlgorithm.py)
#   ALGO_TYPE     알고리즘 클래스명 (default: 파일명 stem)
#   DATA_FOLDER   LEAN 데이터 루트 (default: LEAN 레퍼런스 repo의 Data — 한국 데이터 ETL 전까지 임시)
#   DOTNET_ROOT   .NET SDK 위치 (default: ~/.dotnet)
set -euo pipefail

# --- 경로 기준 ---
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

LEAN_PKG_VERSION="2.5.17757"   # net10 호환 계보. CLAUDE.md §6 참조 (10730.x 금지)

# --- .NET ---
export DOTNET_ROOT="${DOTNET_ROOT:-$HOME/.dotnet}"
export PATH="$DOTNET_ROOT:$PATH"
export DOTNET_CLI_TELEMETRY_OPTOUT=1
command -v dotnet >/dev/null || { echo "ERROR: dotnet 없음 ($DOTNET_ROOT). .NET 10 SDK 설치 필요"; exit 1; }

# --- 전략 인자 ---
STRATEGY="${STRATEGY:-strategies/SmokeTestAlgorithm.py}"
STRATEGY_ABS="$(cd "$(dirname "$STRATEGY")" && pwd)/$(basename "$STRATEGY")"
[ -f "$STRATEGY_ABS" ] || { echo "ERROR: 전략 파일 없음: $STRATEGY_ABS"; exit 1; }
ALGO_TYPE="${ALGO_TYPE:-$(basename "$STRATEGY" .py)}"
STRATEGY_DIR="$(dirname "$STRATEGY_ABS")"

# --- 데이터 루트 (한국 데이터 ETL 전까지는 LEAN 레퍼런스 Data 사용) ---
DATA_FOLDER="${DATA_FOLDER:-/Users/al03044447/IdeaProjects/Lean/Data}"
[ -d "$DATA_FOLDER" ] || { echo "ERROR: 데이터 폴더 없음: $DATA_FOLDER (DATA_FOLDER로 지정하세요)"; exit 1; }

# --- Python 3.11 런타임 (LEAN pythonnet은 3.11 사용) ---
command -v python3.11 >/dev/null || { echo "ERROR: python3.11 없음. 'brew install python@3.11'"; exit 1; }
PY_LIBDIR="$(python3.11 -c 'import sysconfig; print(sysconfig.get_config_var("LIBDIR"))')"
export PYTHONNET_PYDLL="$PY_LIBDIR/libpython3.11.dylib"
[ -f "$PYTHONNET_PYDLL" ] || { echo "ERROR: libpython3.11.dylib 못 찾음: $PYTHONNET_PYDLL"; exit 1; }

# LEAN Python 연동에 필요한 pandas/numpy를 담은 전용 venv (없으면 생성)
LEANPY="$REPO_ROOT/.leanpy"
if [ ! -x "$LEANPY/bin/python" ]; then
  echo ">> LEAN Python 런타임 venv 생성 ($LEANPY)"
  uv venv --python 3.11 "$LEANPY"
  uv pip install --python "$LEANPY/bin/python" pandas numpy
fi
SITE_PACKAGES="$("$LEANPY/bin/python" -c 'import site; print(site.getsitepackages()[0])')"

# --- 빌드 ---
echo ">> 런처 빌드"
dotnet build "$REPO_ROOT/launcher/BuylowLauncher.csproj" -c Release --nologo -v quiet
OUT="$REPO_ROOT/launcher/bin/Release/net10.0"

# AlgorithmImports.py 는 QuantConnect.Common NuGet content에 들어있음 → PYTHONPATH에 추가
AI_DIR="$HOME/.nuget/packages/quantconnect.common/$LEAN_PKG_VERSION/content"
[ -f "$AI_DIR/AlgorithmImports.py" ] || { echo "ERROR: AlgorithmImports.py 못 찾음: $AI_DIR"; exit 1; }

# 'from AlgorithmImports import *' + 전략 import 해소
export PYTHONPATH="$SITE_PACKAGES:$AI_DIR:$STRATEGY_DIR"

# --- config 렌더링 (placeholder 치환) ---
RUN_CONFIG="$OUT/config.json"
sed -e "s#__ALGORITHM_TYPE__#$ALGO_TYPE#g" \
    -e "s#__ALGORITHM_LOCATION__#$STRATEGY_ABS#g" \
    -e "s#__DATA_FOLDER__#$DATA_FOLDER#g" \
    "$REPO_ROOT/launcher/config.json" > "$RUN_CONFIG"

# --- 실행 ---
echo ">> 백테스트 실행: $ALGO_TYPE ($STRATEGY_ABS)"
echo "   data-folder=$DATA_FOLDER  python=3.11  lean=$LEAN_PKG_VERSION"
echo "----------------------------------------------------------------"
cd "$OUT"
dotnet BuylowLauncher.dll
