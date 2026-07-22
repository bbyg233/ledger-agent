from __future__ import annotations

from dataclasses import asdict
from typing import Any, Callable

from agent.contracts import ToolCall, ToolResult


CheckpointHook = Callable[[dict[str, Any]], None]


def serialize_tool_history(
    calls: list[ToolCall],
    results: list[ToolResult],
) -> dict[str, Any]:
    return {
        "calls": [asdict(call) for call in calls],
        "results": [
            {
                "call_id": result.call_id,
                "tool_name": result.tool_name,
                "ok": result.ok,
                "output": result.output,
                "error_type": result.error_type,
                "error_message": result.error_message,
                "duration_ms": result.duration_ms,
            }
            for result in results
        ],
    }


def restore_tool_history(value: Any) -> tuple[list[ToolCall], list[ToolResult]]:
    if not isinstance(value, dict):
        return [], []
    calls = [ToolCall(**item) for item in value.get("calls", []) if isinstance(item, dict)]
    results = [
        ToolResult(
            call_id=str(item.get("call_id") or ""),
            tool_name=str(item.get("tool_name") or ""),
            ok=bool(item.get("ok")),
            output=item.get("output") if isinstance(item.get("output"), dict) else {},
            error_type=str(item.get("error_type") or ""),
            error_message=str(item.get("error_message") or ""),
            duration_ms=max(0, int(item.get("duration_ms") or 0)),
        )
        for item in value.get("results", [])
        if isinstance(item, dict)
    ]
    return calls, results
