#!/usr/bin/env bash
# buylow KIS 라이브 어댑터(adapter/MyTrading.Kis) 빌드 + 런처 출력폴더로 DLL 복사.
#
# 왜 복사하나: LEAN Composer는 config.json의 핸들러(KisBrokerage 등)를 '이름'으로 로드하므로,
# 어댑터 DLL이 런처 실행 디렉토리(launcher/bin/Release/net10.0)에 있어야 한다. 런처 자체는
# 무수정 원칙이라 csproj 참조로 묶지 않고, 빌드 산출물만 이 스크립트로 옆에 떨군다.
#
# 라이브 실주문은 위험하므로, 이 스크립트는 '빌드/배치'만 한다(무장·주문은 대시보드/설정에서).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ADAPTER_CSPROJ="$REPO_ROOT/adapter/MyTrading.Kis/MyTrading.Kis.csproj"
LAUNCHER_OUT="$REPO_ROOT/launcher/bin/Release/net10.0"

export DOTNET_ROOT="${DOTNET_ROOT:-$HOME/.dotnet}"
export PATH="$DOTNET_ROOT:$PATH"
export DOTNET_CLI_TELEMETRY_OPTOUT=1

if [ ! -d "$LAUNCHER_OUT" ]; then
  echo "런처가 아직 빌드되지 않았습니다($LAUNCHER_OUT 없음)." >&2
  echo "먼저 대시보드/백테스트를 한 번 실행하거나 런처를 빌드하세요." >&2
  exit 1
fi

echo ">> 어댑터 빌드 (MyTrading.Kis)"
dotnet build "$ADAPTER_CSPROJ" -c Release --nologo -v quiet

ADAPTER_DLL="$REPO_ROOT/adapter/MyTrading.Kis/bin/Release/net10.0/MyTrading.Kis.dll"
if [ ! -f "$ADAPTER_DLL" ]; then
  echo "빌드 후 어댑터 DLL이 없음: $ADAPTER_DLL" >&2
  exit 1
fi

echo ">> 런처 출력폴더로 복사: $LAUNCHER_OUT"
cp "$ADAPTER_DLL" "$LAUNCHER_OUT/"
echo "완료. live-kis 환경에서 KisBrokerage를 로드할 수 있습니다."
