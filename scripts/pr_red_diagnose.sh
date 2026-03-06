#!/usr/bin/env bash
set -euo pipefail

echo "[1/4] 运行与 CI 一致的 py_compile..."
python - <<'PY'
import py_compile
files = ["app.py", "app_backup_before_sync.py", "project_admin.py"]
failed = False
for f in files:
    try:
        py_compile.compile(f, doraise=True)
        print(f"✅ {f}")
    except Exception as e:
        failed = True
        print(f"❌ {f}\n   {e}")
if failed:
    raise SystemExit(1)
PY


echo "\n[2/4] 运行缩进检查(tabnanny)..."
python -m tabnanny app.py app_backup_before_sync.py project_admin.py
echo "✅ 缩进检查通过"

echo "\n[3/4] 检查冲突标记..."
if rg -n "^(<<<<<<<|=======|>>>>>>>)" --glob '!*.lock' .; then
  echo "❌ 发现冲突标记"
  exit 2
else
  echo "✅ 无冲突标记"
fi

echo "\n[4/4] 本地诊断通过（若 GitHub 仍红，通常是远端分支未包含本地修复 commit）"
