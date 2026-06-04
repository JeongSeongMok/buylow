#!/usr/bin/env bash
# 분봉 시드 데이터 올리기 (메인테이너용)
#
# 현재 data/equity/krx/minute 를 묶어 GitHub 릴리스(고정 태그 minute-seed)에 올린다.
# 사용자는 scripts/fetch_minute_seed.sh 로 이걸 받는다. 데이터는 레포 히스토리에 쌓지 않고
# 릴리스 에셋으로 두므로(클론은 가볍게 유지), 갱신할 때마다 같은 태그에 --clobber로 교체한다 —
# 분봉 zip은 write-once라 더 최신·더 많은 종목으로 통째 갈아끼워도 안전하다.
#
# 요구: gh(GitHub CLI) 로그인 상태. 커밋 올릴 때마다 최신/최대 종목으로 함께 실행 권장.
set -euo pipefail

REPO="${BUYLOW_REPO:-JeongSeongMok/buylow}"
TAG="${BUYLOW_SEED_TAG:-minute-seed}"
ASSET="minute-seed.tar.gz"

ROOT="$(git -C "$(dirname "$0")" rev-parse --show-toplevel)"
cd "$ROOT"

SRC="data/equity/krx/minute"
[ -d "$SRC" ] || { echo "분봉 데이터가 없습니다: ${ROOT}/${SRC}" >&2; exit 1; }

count=$(ls "$SRC" | wc -l | tr -d ' ')
files=$(find "$SRC" -name '*.zip' | wc -l | tr -d ' ')
echo "분봉 종목 ${count}개 / zip ${files}개 묶는 중…"

tmpdir="$(mktemp -d)"
trap 'rm -rf "$tmpdir"' EXIT
out="${tmpdir}/${ASSET}"
# 레포 루트 기준 경로로 담아(fetch가 그대로 풀 수 있게) 묶는다.
tar -czf "$out" "$SRC"
size=$(du -h "$out" | cut -f1)
echo "묶음 크기: ${size}"

notes="백테스트용 LEAN 분봉 시드 묶음. \`scripts/fetch_minute_seed.sh\` 로 받습니다.
종목 ${count}개 · zip ${files}개 · ${size}. (KIS 보관 한계로 최근 약 1년 범위)"

# 릴리스가 있으면 에셋만 교체, 없으면 생성.
if gh release view "$TAG" --repo "$REPO" >/dev/null 2>&1; then
  gh release upload "$TAG" "$out" --repo "$REPO" --clobber
  gh release edit "$TAG" --repo "$REPO" --notes "$notes"
else
  gh release create "$TAG" "$out" --repo "$REPO" \
    --title "분봉 시드 데이터" --notes "$notes"
fi
echo "업로드 완료: ${REPO} @ ${TAG} / ${ASSET}"
