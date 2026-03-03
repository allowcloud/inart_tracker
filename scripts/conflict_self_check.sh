#!/usr/bin/env bash
set -euo pipefail

TARGET_BRANCH="${1:-main}"
EXIT_CODE=0

echo "[1/5] 检查未解决的 merge markers..."
if rg -n "^(<<<<<<<|=======|>>>>>>>)" --glob '!*.lock' . >/tmp/conflict_markers.txt; then
  echo "❌ 发现未解决冲突标记:"
  cat /tmp/conflict_markers.txt
  EXIT_CODE=2
else
  echo "✅ 未发现 merge markers"
fi

echo

echo "[2/5] 识别目标分支..."
if git show-ref --verify --quiet "refs/heads/${TARGET_BRANCH}"; then
  TARGET_REF="${TARGET_BRANCH}"
elif git show-ref --verify --quiet "refs/remotes/origin/${TARGET_BRANCH}"; then
  TARGET_REF="origin/${TARGET_BRANCH}"
else
  echo "⚠️ 未找到 ${TARGET_BRANCH} / origin/${TARGET_BRANCH}，将跳过预测合并冲突检查"
  TARGET_REF=""
fi

echo

echo "[3/5] 检查当前分支改动规模（冲突风险代理指标）..."
CHANGED_FILES=$(git diff --name-only "${TARGET_REF:-HEAD~1}"...HEAD 2>/dev/null || git diff --name-only HEAD~1...HEAD)
if [[ -z "${CHANGED_FILES}" ]]; then
  echo "✅ 未检测到相对变更"
else
  echo "变更文件:"
  echo "${CHANGED_FILES}" | sed 's/^/ - /'
fi

if echo "${CHANGED_FILES}" | grep -q '^app.py$'; then
  HUNK_COUNT=$(git diff -U0 "${TARGET_REF:-HEAD~1}"...HEAD -- app.py 2>/dev/null | rg '^@@' -n | wc -l | tr -d ' ')
  echo "app.py 变更块数: ${HUNK_COUNT}"
  if [[ "${HUNK_COUNT}" -ge 8 ]]; then
    echo "⚠️ app.py 是单文件热点，且变更块 >= 8，冲突风险较高"
  else
    echo "✅ app.py 变更块数量可控"
  fi
fi

echo

echo "[4/5] 预测与 ${TARGET_REF:-<unknown>} 的合并冲突..."
if [[ -n "${TARGET_REF}" ]]; then
  BASE=$(git merge-base HEAD "${TARGET_REF}")
  MERGE_TREE_OUT=$(git merge-tree "$BASE" HEAD "${TARGET_REF}" || true)
  if echo "$MERGE_TREE_OUT" | rg -n "^(<<<<<<<|=======|>>>>>>>)" >/tmp/predicted_conflicts.txt; then
    echo "❌ 预测到潜在合并冲突（merge-tree）:"
    cat /tmp/predicted_conflicts.txt
    EXIT_CODE=3
  else
    echo "✅ 未预测到文本冲突"
  fi
fi

echo

echo "[5/5] 冲突前置自检结论"
if [[ "$EXIT_CODE" -eq 0 ]]; then
  echo "✅ PASS: 未发现阻断性冲突风险"
else
  echo "❌ FAIL: 请先解决上述问题再发起/更新 PR"
fi

exit "$EXIT_CODE"
