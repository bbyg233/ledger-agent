#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$project_dir/scripts/runtime.sh"
python_bin="$(ledger_python)"
log_file="$project_dir/.financial_agent/web.log"
web_port="${LEDGER_AGENT_PORT:-8000}"
proxy_url="$(ledger_outbound_proxy)"
proxy_env=()

if [[ -n "$proxy_url" ]]; then
  no_proxy="${NO_PROXY:+${NO_PROXY},}127.0.0.1,localhost,::1"
  proxy_env=(
    "HTTP_PROXY=$proxy_url"
    "HTTPS_PROXY=$proxy_url"
    "ALL_PROXY=$proxy_url"
    "NO_PROXY=$no_proxy"
  )
  printf '使用 WSL 出站代理: %s\n' "$proxy_url" >>"$log_file"
fi

cd "$project_dir"
exec env PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 "${proxy_env[@]}" "$python_bin" -m uvicorn web_app:app \
  --app-dir src --host 127.0.0.1 --port "$web_port" >>"$log_file" 2>&1
