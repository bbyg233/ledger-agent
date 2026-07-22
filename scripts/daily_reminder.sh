#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
reminder_url="${LEDGER_REMINDER_URL:-http://127.0.0.1:8000/?view=chat&reminder=daily}"
runtime_dir="$project_dir/.financial_agent"
reminder_log="$runtime_dir/reminder.log"

if [[ "${1:-}" == "--dry-run" ]]; then
  printf 'project=%s\nurl=%s\n' "$project_dir" "$reminder_url"
  exit 0
fi

mkdir -p "$runtime_dir"
exec >>"$reminder_log" 2>&1
printf '%s reminder started\n' "$(date '+%Y-%m-%d %H:%M:%S')"

bash "$project_dir/scripts/start_web.sh"

if [[ "${LEDGER_REMINDER_NO_BROWSER:-0}" == "1" ]]; then
  :
elif [[ -x /mnt/c/Windows/explorer.exe ]]; then
  /mnt/c/Windows/explorer.exe "$reminder_url" >/dev/null 2>&1 || true
elif command -v xdg-open >/dev/null 2>&1; then
  xdg-open "$reminder_url" >/dev/null 2>&1 || true
else
  printf '记账页面已就绪：%s\n' "$reminder_url"
fi

printf '%s reminder completed\n' "$(date '+%Y-%m-%d %H:%M:%S')"
