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
