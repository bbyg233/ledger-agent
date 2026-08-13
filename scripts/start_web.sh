#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$project_dir/scripts/runtime.sh"
runtime_dir="$project_dir/.financial_agent"
pid_file="$runtime_dir/web.pid"
log_file="$runtime_dir/web.log"
web_port="${LEDGER_AGENT_PORT:-8000}"
web_url="http://127.0.0.1:${web_port}"
health_url="${web_url}/api/health"
systemd_unit="ledger-agent-web.service"
use_systemd=0

# WSL's transient user manager may exit after the launcher command returns.
# Keep the Web UI in a detached session by default; systemd remains opt-in.
if [[ "${LEDGER_AGENT_USE_SYSTEMD:-0}" == "1" ]] && systemctl --user show-environment >/dev/null 2>&1; then
  use_systemd=1
fi

is_live_pid() {
  local pid="$1" state
  [[ -r "/proc/$pid/stat" ]] || return 1
  state="$(awk '{print $3}' "/proc/$pid/stat" 2>/dev/null || true)"
  [[ -n "$state" && "$state" != "Z" ]] || return 1
}

is_project_web_pid() {
  local pid="$1" cwd cmdline
  is_live_pid "$pid" || return 1
  cwd="$(readlink -f "/proc/$pid/cwd" 2>/dev/null || true)"
  cmdline="$(tr '\0' ' ' <"/proc/$pid/cmdline" 2>/dev/null || true)"
  [[ "$cwd" == "$project_dir" && "$cmdline" == *"uvicorn"* && "$cmdline" == *"web_app:app"* ]]
}

mkdir -p "$runtime_dir"
if [[ "$use_systemd" -eq 1 ]] && systemctl --user is-active --quiet "$systemd_unit" 2>/dev/null; then
  if curl --noproxy '*' -fsS "$health_url" >/dev/null; then
    existing_pid="$(systemctl --user show "$systemd_unit" -p MainPID --value)"
    echo "$existing_pid" >"$pid_file"
    echo "Web UI 已在运行: ${web_url} (PID $existing_pid)"
    exit 0
  fi
  systemctl --user stop "$systemd_unit"
fi

if [[ -f "$pid_file" ]]; then
  existing_pid="$(tr -d '[:space:]' <"$pid_file")"
  if is_project_web_pid "$existing_pid" && curl --noproxy '*' -fsS "$health_url" >/dev/null; then
    echo "Web UI 已在运行: ${web_url} (PID $existing_pid)"
    exit 0
  fi
  rm -f "$pid_file"
fi

cd "$project_dir"
: >"$log_file"
if [[ "$use_systemd" -eq 1 ]]; then
  systemd-run --user --unit="$systemd_unit" --collect --quiet \
    --working-directory="$project_dir" "$project_dir/scripts/run_web_foreground.sh"
  pid="$(systemctl --user show "$systemd_unit" -p MainPID --value)"
else
  setsid "$project_dir/scripts/run_web_foreground.sh" </dev/null >"$log_file" 2>&1 &
  pid=$!
fi
echo "$pid" >"$pid_file"

for _ in $(seq 1 50); do
  if curl --noproxy '*' -fsS "$health_url" >/dev/null 2>&1; then
    sleep 0.1
    if is_project_web_pid "$pid" && curl --noproxy '*' -fsS "$health_url" >/dev/null 2>&1; then
      echo "Web UI 已启动: ${web_url} (PID $pid)"
      echo "日志: $log_file"
      exit 0
    fi
  fi
  if ! is_live_pid "$pid"; then
    if systemctl --user is-active --quiet "$systemd_unit" 2>/dev/null; then
      pid="$(systemctl --user show "$systemd_unit" -p MainPID --value)"
      echo "$pid" >"$pid_file"
      continue
    fi
    break
  fi
  sleep 0.2
done

rm -f "$pid_file"
echo "Web UI 启动失败，日志如下：" >&2
sed -n '1,120p' "$log_file" >&2
exit 1
