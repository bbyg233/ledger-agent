#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$project_dir/scripts/runtime.sh"
python_bin="$(ledger_python)"
log_file="$project_dir/.financial_agent/web.log"

cd "$project_dir"
exec env PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 "$python_bin" -m uvicorn web_app:app \
  --app-dir src --host 127.0.0.1 --port 8000 >>"$log_file" 2>&1
