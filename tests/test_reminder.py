from datetime import datetime

from financial_agent import connect, get_preferences, init_db
from agent import AgentRunner, ToolCall, ToolExecutionContext
from agent.tools import build_tool_registry
from services.reminder import (
    REMINDER_PREFERENCE_KEY,
    claim_due_reminder,
    reminder_view,
    set_reminder_skip_for_today,
    update_reminder_settings,
)


def test_reminder_claims_once_after_scheduled_time():
    before = datetime(2026, 8, 9, 21, 59)
    due = datetime(2026, 8, 9, 22, 0)
    settings = update_reminder_settings({}, reminder_time="22:00", enabled=True)

    should_open, unchanged, reason = claim_due_reminder(settings, before)
    assert (should_open, reason) == (False, "before_time")
    assert unchanged["sent_on"] == ""

    should_open, claimed, reason = claim_due_reminder(settings, due)
    assert (should_open, reason) == (True, "due")
    assert claimed["sent_on"] == "2026-08-09"

    should_open, _, reason = claim_due_reminder(claimed, due)
    assert (should_open, reason) == (False, "already_sent")


def test_skip_today_prevents_only_current_day():
    today = datetime(2026, 8, 9, 22, 1)
    tomorrow = datetime(2026, 8, 10, 22, 1)
    skipped = set_reminder_skip_for_today({"time": "22:00"}, skip=True, now=today)

    assert reminder_view(skipped, today)["skipped_today"] is True
    should_open, _, reason = claim_due_reminder(skipped, today)
    assert (should_open, reason) == (False, "skipped_today")

    should_open, claimed, reason = claim_due_reminder(skipped, tomorrow)
    assert (should_open, reason) == (True, "due")
    assert claimed["sent_on"] == "2026-08-10"


def test_agent_reminder_tool_only_updates_reminder_preference(tmp_path, monkeypatch):
    monkeypatch.setattr("agent.tools.schedule_windows_reminder_sync", lambda *_args, **_kwargs: False)
    conn = connect(tmp_path / "ledger.db")
    init_db(conn)
    context = ToolExecutionContext(state=conn, run_id="reminder-test")

    result = AgentRunner(build_tool_registry()).run(
        [
            ToolCall(
                name="manage_daily_reminder",
                arguments={"time": "21:00", "skip_today": True},
            )
        ],
        context,
    )[0]

    assert result.ok is True
    assert result.output["reminder"]["time"] == "21:00"
    assert result.output["reminder"]["skipped_today"] is True
    assert get_preferences(conn)[REMINDER_PREFERENCE_KEY]["time"] == "21:00"
    assert conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 0
