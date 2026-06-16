#!/usr/bin/env bash
# buylow 라이브 어댑터 빌드 + 런처 출력폴더로 DLL 복사.
#
# 어댑터는 두 개:
#   - adapter/MyTrading.Kis  → MyTrading.Kis.dll  (한국투자증권)
#   - adapter/MyTrading.Toss → MyTrading.Toss.dll (토스증권)
# 둘 다 빌드해 런처 옆에 떨군다. 인자로 'kis' 또는 'toss'를 주면 그 하나만 빌드한다.
#
# 왜 복사하나: LEAN Composer는 config.json의 핸들러(KisBrokerage/TossBrokerage)를 '이름'으로
# 로드하므로, 어댑터 DLL이 런처 실행 디렉토리(launcher/bin/Release/net10.0)에 있어야 한다. 런처
# 자체는 무수정 원칙이라 csproj 참조로 묶지 않고, 빌드 산출물만 이 스크립트로 옆에 떨군다.
#
# 라이브 실주문은 위험하므로, 이 스크립트는 '빌드/배치'만 한다(자동매매 토글은 대시보드 매매 탭에서).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LAUNCHER_OUT="$REPO_ROOT/launcher/bin/Release/net10.0"

export DOTNET_ROOT="${DOTNET_ROOT:-$HOME/.dotnet}"
export PATH="$DOTNET_ROOT:$PATH"
export DOTNET_CLI_TELEMETRY_OPTOUT=1

if [ ! -d "$LAUNCHER_OUT" ]; then
  echo "런처가 아직 빌드되지 않았습니다($LAUNCHER_OUT 없음)." >&2
  echo "먼저 대시보드/백테스트를 한 번 실행하거나 런처를 빌드하세요." >&2
  exit 1
fi

# 빌드 대상: 인자 없으면 둘 다(kis toss).
TARGETS=("kis" "toss")
if [ "$#" -ge 1 ]; then
  TARGETS=("$@")
fi

build_one() {
  local name="$1" proj dll
  case "$name" in
    kis)  proj="MyTrading.Kis";  dll="MyTrading.Kis.dll" ;;
    toss) proj="MyTrading.Toss"; dll="MyTrading.Toss.dll" ;;
    *) echo "알 수 없는 어댑터: $name (가능: kis, toss)" >&2; exit 1 ;;
  esac
  local csproj="$REPO_ROOT/adapter/$proj/$proj.csproj"
  echo ">> 어댑터 빌드 ($proj)"
  dotnet build "$csproj" -c Release --nologo -v quiet
  local out="$REPO_ROOT/adapter/$proj/bin/Release/net10.0/$dll"
  if [ ! -f "$out" ]; then
    echo "빌드 후 어댑터 DLL이 없음: $out" >&2
    exit 1
  fi
  cp "$out" "$LAUNCHER_OUT/"
  echo "   복사: $LAUNCHER_OUT/$dll"
}

for t in "${TARGETS[@]}"; do
  build_one "$t"
done
echo "완료. live-kis(KisBrokerage)/live-toss(TossBrokerage) 환경에서 어댑터를 로드할 수 있습니다."
