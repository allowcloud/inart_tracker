# 冲突防复发：提交前自检规范

> 适用范围：本仓库（尤其是 `app.py` 单文件热点）。

## 为什么会频繁冲突（Deep Dive 结论）

1. **热点集中在 `app.py`**：同一文件承载大量模块（看板、特定项目、AI速记、系统维护），多人并行改动时容易命中同一区段。
2. **功能改动跨模块联动**：例如“暂停态”会同时影响自动同步、矩阵、甘特、AI入库，PR 改动跨度大导致 hunk overlap。
3. **占位文案分叉**：类似“未知/冲突”文案和分支逻辑如果重复维护，极易出现并行修改冲突。
4. **缺少提交前冲突预测**：过去没有固定跑 `merge-tree` 预判，往往在 PR merge 时才暴露。

## 现在固定流程（每次提交前）

```bash
scripts/conflict_self_check.sh
# 或指定目标分支
scripts/conflict_self_check.sh main
```

脚本会做 5 件事：

1. 检查仓库里是否还有 `<<<<<<< / ======= / >>>>>>>`。
2. 自动识别目标分支（`main` 或 `origin/main`）。
3. 统计改动文件与 `app.py` 变更块数（风险代理）。
4. 用 `git merge-tree` 做“预合并冲突预测”。
5. 给出 PASS/FAIL 结论（非 0 退出码表示应先修再提 PR）。

## 团队协作建议（高优先级）

- 大改动尽量拆 PR：`暂停态`、`AI速记`、`系统维护`分开提。
- 修改 `app.py` 时，避免同时触碰“helper 区 + 菜单区 + 大循环区”。
- 文案常量（例如手动选择 sentinel）统一为单一常量，避免复制分叉。
- 每次 rebase 后**再跑一次** `scripts/conflict_self_check.sh`。

## 退出准则

满足以下条件才允许发起/更新 PR：

- 自检脚本返回 PASS。
- 无 merge markers。
- `python -m py_compile app.py` 通过。


## 一键“强制走 rebase 流程”（推荐）

> 说明：Git 不能保证“绝对零冲突”，但可以通过 `rerere + rebase strategy` 最大化自动化，减少重复手工解冲突。

```bash
# 默认：rebase 到 main，且 -X theirs（优先应用当前分支补丁）
scripts/rebase_merge_guard.sh

# 指定目标分支
scripts/rebase_merge_guard.sh main

# 指定策略（theirs / ours / none）
scripts/rebase_merge_guard.sh main theirs
```

这个脚本会：

1. 强制要求工作区干净（防止脏状态放大冲突）。
2. 自动开启 `rerere`（记住你解过的冲突，下次自动复用）。
3. 先跑一次 `conflict_self_check` 再 rebase。
4. rebase 后再跑一次 `conflict_self_check`。

当你们的冲突集中在同几段（本仓库确实如此）时，`rerere` 会显著减少“重复报同样冲突”。
