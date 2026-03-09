#!/usr/bin/env bash
set -euo pipefail

echo "[1/6] Running py_compile on key files..."
python - <<'PY'
import py_compile
files = ["app.py", "app_backup_before_sync.py", "project_admin.py"]
failed = False
for f in files:
    try:
        py_compile.compile(f, doraise=True)
        print(f"OK  {f}")
    except Exception as e:
        failed = True
        print(f"ERR {f}\n   {e}")
if failed:
    raise SystemExit(1)
PY

echo "\n[2/6] Running repository-wide Python compile check..."
python - <<'PY'
import py_compile, subprocess
files = subprocess.check_output(['git', 'ls-files', '*.py'], text=True).splitlines()
for f in files:
    py_compile.compile(f, doraise=True)
print(f"OK  compiled {len(files)} python files")
PY

echo "\n[3/6] Running indentation check (tabnanny)..."
python -m tabnanny app.py app_backup_before_sync.py project_admin.py
echo "OK  indentation check passed"

echo "\n[4/6] Running AST parse check (app.py)..."
python - <<'PY'
import ast
from pathlib import Path
ast.parse(Path('app.py').read_text(encoding='utf-8'))
print('OK  app.py AST parse passed')
PY

echo "\n[5/6] Checking conflict markers..."
if rg -n "^(<<<<<<<|=======|>>>>>>>)" --glob '!*.lock' .; then
  echo "ERR conflict markers found"
  exit 2
else
  echo "OK  no conflict markers"
fi

echo "\n[6/6] Local diagnose passed"
