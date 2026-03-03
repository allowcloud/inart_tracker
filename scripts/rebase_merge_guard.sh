#!/usr/bin/env bash
set -euo pipefail

TARGET_BRANCH="${1:-main}"
STRATEGY="${2:-theirs}"   # theirs|ours|none

if [[ -n "$(git status --porcelain)" ]]; then
  echo "❌ 工作区不干净，请先 commit 或 stash。"
  exit 2
fi

# 让 Git 记住你解决过的冲突，下次自动复用
git config rerere.enabled true
git config rerere.autoupdate true
git config merge.conflictstyle zdiff3

echo "[1/4] 定位目标分支 ${TARGET_BRANCH}..."
if git show-ref --verify --quiet "refs/remotes/origin/${TARGET_BRANCH}"; then
  git fetch origin "${TARGET_BRANCH}" --quiet || true
  TARGET_REF="origin/${TARGET_BRANCH}"
elif git show-ref --verify --quiet "refs/heads/${TARGET_BRANCH}"; then
  TARGET_REF="${TARGET_BRANCH}"
else
  echo "❌ 找不到 ${TARGET_BRANCH} 或 origin/${TARGET_BRANCH}"
  exit 3
fi

echo "[2/4] 预检查冲突风险..."
bash scripts/conflict_self_check.sh "${TARGET_BRANCH}" || true

echo "[3/4] 执行 rebase 到 ${TARGET_REF}..."
if [[ "${STRATEGY}" == "none" ]]; then
  git rebase --rebase-merges --autostash "${TARGET_REF}"
else
  git rebase --rebase-merges --autostash -X "${STRATEGY}" "${TARGET_REF}"
fi

echo "[4/4] rebase 后再次自检..."
bash scripts/conflict_self_check.sh "${TARGET_BRANCH}" || true

echo "✅ 完成。已启用 rerere，后续同类冲突会自动套用历史解法。"
