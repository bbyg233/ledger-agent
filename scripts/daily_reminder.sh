#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$project_dir/scripts/runtime.sh"
web_port="${LEDGER_AGENT_PORT:-8000}"
runtime_dir="$project_dir/.financial_agent"
reminder_log="$runtime_dir/reminder.log"
python_bin="$(ledger_python)"

if [[ "${1:-}" == "--dry-run" ]]; then
  printf 'project=%s\nport=%s\nreminder_check=enabled\n' "$project_dir" "$web_port"
  exit 0
fi

mkdir -p "$runtime_dir"

should_open="$({
  cd "$project_dir"
  PYTHONPATH="$project_dir/src" "$python_bin" - <<'PY'
from financial_agent import connect, get_preferences, init_db, set_preference
from services.reminder import (
    REMINDER_PREFERENCE_KEY,
    claim_due_reminder,
    normalize_reminder_settings,
)
import os

conn = connect()
try:
    init_db(conn)
    settings = get_preferences(conn).get(REMINDER_PREFERENCE_KEY, {})
    if not isinstance(settings, dict) or not settings:
        settings = normalize_reminder_settings(
            {"time": os.environ.get("LEDGER_REMINDER_DEFAULT_TIME", "")}
        )
        set_preference(conn, REMINDER_PREFERENCE_KEY, settings)
    due, updated, reason = claim_due_reminder(settings)
    if due:
        set_preference(conn, REMINDER_PREFERENCE_KEY, updated)
    print("open" if due else f"skip:{reason}")
finally:
    conn.close()
PY
} 2>&1)"

if [[ "$should_open" == skip:* ]]; then
  exit 10
fi
if [[ "$should_open" != "open" ]]; then
  printf '%s reminder check failed: %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$should_open" >>"$reminder_log"
  exit 1
fi

exec >>"$reminder_log" 2>&1
printf '%s reminder started\n' "$(date '+%Y-%m-%d %H:%M:%S')"

bash "$project_dir/scripts/start_web.sh"

printf '%s reminder completed\n' "$(date '+%Y-%m-%d %H:%M:%S')"
