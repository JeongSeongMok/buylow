#!/usr/bin/env bash
# 분봉 시드 데이터 받기 (사용자용)
#
# 백테스트의 장중 타이밍(②) 검증에는 분봉이 필요한데, KIS로 전종목 분봉을 새로 받으면
# 호출이 많아 오래 걸린다. 그래서 레포가 GitHub 릴리스로 '시드' 묶음을 제공한다 —
# 이 스크립트로 받아 data/ 에 풀면 바로 백테스트가 가능하고, 부족한 종목·기간은 대시보드
# '분봉 최신화' 버튼으로 증분 채우면 된다(이미 있는 날짜는 호출 없이 건너뜀).
#
# gh/토큰 불필요 — 공개 릴리스 에셋을 curl로 직접 받는다.
set -euo pipefail

REPO="${BUYLOW_REPO:-JeongSeongMok/buylow}"   # 포크했다면 BUYLOW_REPO로 덮어쓰기
TAG="${BUYLOW_SEED_TAG:-minute-seed}"          # 시드 묶음을 담는 고정 릴리스 태그
ASSET="minute-seed.tar.gz"
URL="https://github.com/${REPO}/releases/download/${TAG}/${ASSET}"

ROOT="$(git -C "$(dirname "$0")" rev-parse --show-toplevel)"
cd "$ROOT"

echo "분봉 시드 다운로드: ${URL}"
tmpdir="$(mktemp -d)"
trap 'rm -rf "$tmpdir"' EXIT
if ! curl -fL --progress-bar -o "${tmpdir}/${ASSET}" "$URL"; then
  echo "다운로드 실패 — 릴리스/에셋이 아직 없을 수 있습니다: ${URL}" >&2
  echo "(메인테이너는 scripts/make_minute_seed.sh 로 먼저 올리세요.)" >&2
  exit 1
fi

# 레포 루트 기준 경로(data/equity/krx/minute/...)로 담겨 있어 그대로 풀면 제자리에 놓인다.
# 같은 날짜 파일은 write-once라 내용이 동일 → 덮어써도 무방. 사용자가 추가로 받은 날은 보존.
echo "data/ 에 푸는 중…"
tar -xzf "${tmpdir}/${ASSET}" -C "$ROOT"
# LEAN이 기대하는 빈 보조 디렉토리 보장(시드에 없을 수 있음).
mkdir -p data/equity/krx/map_files data/equity/krx/factor_files

n=$(ls data/equity/krx/minute 2>/dev/null | wc -l | tr -d ' ')
echo "완료. 분봉 적재 종목 수: ${n}"
