from __future__ import annotations

import re
from datetime import datetime
from typing import Any


REMINDER_PREFERENCE_KEY = "daily_reminder"
DEFAULT_REMINDER_TIME = "22:00"
_TIME_PATTERN = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")


def normalize_reminder_settings(value: Any) -> dict[str, Any]:
    """Return a stable, backward-compatible daily reminder configuration."""
    raw = value if isinstance(value, dict) else {}
    reminder_time = str(raw.get("time") or DEFAULT_REMINDER_TIME).strip()
    if not _TIME_PATTERN.fullmatch(reminder_time):
        reminder_time = DEFAULT_REMINDER_TIME
    return {
        "enabled": bool(raw.get("enabled", True)),
        "time": reminder_time,
        "skipped_on": str(raw.get("skipped_on") or ""),
        "sent_on": str(raw.get("sent_on") or ""),
    }


def reminder_view(settings: Any, now: datetime | None = None) -> dict[str, Any]:
    current = now or datetime.now()
    normalized = normalize_reminder_settings(settings)
    today = current.date().isoformat()
    return {
        "enabled": normalized["enabled"],
        "time": normalized["time"],
        "skipped_today": normalized["skipped_on"] == today,
        "sent_today": normalized["sent_on"] == today,
        "today": today,
    }


def update_reminder_settings(
    settings: Any,
    *,
    enabled: bool | None = None,
    reminder_time: str | None = None,
) -> dict[str, Any]:
    updated = normalize_reminder_settings(settings)
    if enabled is not None:
        updated["enabled"] = bool(enabled)
    if reminder_time is not None:
        candidate = reminder_time.strip()
        if not _TIME_PATTERN.fullmatch(candidate):
            raise ValueError("提醒时间必须是 HH:MM 格式")
        updated["time"] = candidate
    return updated


def set_reminder_skip_for_today(settings: Any, *, skip: bool, now: datetime | None = None) -> dict[str, Any]:
    current = now or datetime.now()
    updated = normalize_reminder_settings(settings)
    updated["skipped_on"] = current.date().isoformat() if skip else ""
    return updated


def claim_due_reminder(settings: Any, now: datetime | None = None) -> tuple[bool, dict[str, Any], str]:
    """Atomically-style claim a reminder after the caller persists ``updated``.

    The scheduled task may check frequently. Once a check returns ``True``, the
    caller saves the returned settings, preventing another browser launch today.
    """
    current = now or datetime.now()
    updated = normalize_reminder_settings(settings)
    today = current.date().isoformat()
    if not updated["enabled"]:
        return False, updated, "disabled"
    if updated["skipped_on"] == today:
        return False, updated, "skipped_today"
    if updated["sent_on"] == today:
        return False, updated, "already_sent"
    scheduled = datetime.strptime(updated["time"], "%H:%M").time()
    if current.time().replace(second=0, microsecond=0) < scheduled:
        return False, updated, "before_time"
    updated["sent_on"] = today
    return True, updated, "due"
