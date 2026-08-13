#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
pid_file="$project_dir/.financial_agent/web.pid"
systemd_unit="ledger-agent-web.service"
web_port="${LEDGER_AGENT_PORT:-8000}"
declare -A project_pids=()

is_project_web_pid() {
  local pid="$1" cwd cmdline
  [[ -r "/proc/$pid/cmdline" ]] || return 1
  cwd="$(readlink -f "/proc/$pid/cwd" 2>/dev/null || true)"
  cmdline="$(tr '\0' ' ' <"/proc/$pid/cmdline" 2>/dev/null || true)"
  [[ "$cwd" == "$project_dir" && "$cmdline" == *"uvicorn"* && "$cmdline" == *"web_app:app"* ]]
}

if [[ -f "$pid_file" ]]; then
  pid="$(tr -d '[:space:]' <"$pid_file")"
  if is_project_web_pid "$pid"; then
    project_pids["$pid"]=1
  fi
fi

for proc in /proc/[0-9]*/cmdline; do
  pid="${proc#/proc/}"
  pid="${pid%/cmdline}"
  if is_project_web_pid "$pid"; then
    project_pids["$pid"]=1
  fi
done

if command -v fuser >/dev/null 2>&1; then
  for pid in $(fuser -n tcp "$web_port" 2>/dev/null || true); do
    if is_project_web_pid "$pid"; then
      project_pids["$pid"]=1
    fi
  done
fi

stopped_systemd=0
if systemctl --user is-active --quiet "$systemd_unit" 2>/dev/null; then
  systemctl --user stop "$systemd_unit"
  stopped_systemd=1
fi

if [[ ${#project_pids[@]} -eq 0 && "$stopped_systemd" -eq 0 ]]; then
  rm -f "$pid_file"
  echo "Web UI 未运行"
  exit 0
fi

for pid in "${!project_pids[@]}"; do
  kill "$pid" 2>/dev/null || true
done

for _ in $(seq 1 40); do
  running=0
  for pid in "${!project_pids[@]}"; do
    if is_project_web_pid "$pid"; then
      running=1
    fi
  done
  [[ "$running" -eq 0 ]] && break
  sleep 0.1
done

rm -f "$pid_file"
echo "Web UI 已停止"
