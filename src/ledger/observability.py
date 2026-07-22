from __future__ import annotations

import json
import os
import re
import sqlite3
from datetime import datetime
from typing import Any, Callable, Mapping

from agent.contracts import ToolStep
from agent.usage import ModelUsage


EnsureColumn = Callable[[sqlite3.Connection, str, str, str], None]


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def redact_sensitive_text(value: str) -> str:
    redacted = value
    for name in ("LEDGER_AGENT_API_KEY", "ARK_API_KEY", "GROQ_API_KEY"):
        secret = os.environ.get(name)
        if secret:
            redacted = redacted.replace(secret, "[REDACTED]")
    patterns = (
        r"(?i)(bearer\s+)[A-Za-z0-9._-]{12,}",
        r"(?i)((?:api[_ -]?key|token|secret)\s*[=:]\s*)[^\s,;]{8,}",
        r"(?<!\d)(?:\d[ -]?){15,19}(?!\d)",
        r"(?<!\d)1[3-9]\d{9}(?!\d)",
        r"(?<![A-Za-z0-9])[1-9]\d{5}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[0-9Xx](?![A-Za-z0-9])",
    )
    for pattern in patterns:
        redacted = re.sub(pattern, "[REDACTED]", redacted)
    return redacted


def ensure_observability_schema(
    conn: sqlite3.Connection,
    ensure_column: EnsureColumn,
) -> None:
    for column, definition in (
        ("model_requests", "INTEGER NOT NULL DEFAULT 0"),
        ("input_tokens", "INTEGER NOT NULL DEFAULT 0"),
        ("output_tokens", "INTEGER NOT NULL DEFAULT 0"),
        ("total_tokens", "INTEGER NOT NULL DEFAULT 0"),
        ("cached_tokens", "INTEGER NOT NULL DEFAULT 0"),
        ("reasoning_tokens", "INTEGER NOT NULL DEFAULT 0"),
        ("estimated_cost", "REAL"),
    ):
        ensure_column(conn, "agent_runs", column, definition)
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS agent_model_calls (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            call_index INTEGER NOT NULL CHECK(call_index > 0),
            provider TEXT NOT NULL,
            model TEXT NOT NULL,
            api_style TEXT NOT NULL CHECK(api_style IN ('chat', 'responses')),
            purpose TEXT NOT NULL DEFAULT 'agent',
            input_tokens INTEGER NOT NULL DEFAULT 0,
            output_tokens INTEGER NOT NULL DEFAULT 0,
            total_tokens INTEGER NOT NULL DEFAULT 0,
            cached_tokens INTEGER NOT NULL DEFAULT 0,
            reasoning_tokens INTEGER NOT NULL DEFAULT 0,
            duration_ms INTEGER NOT NULL DEFAULT 0,
            estimated_cost REAL,
            response_id TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            UNIQUE(run_id, call_index)
        );

        CREATE TABLE IF NOT EXISTS agent_checkpoints (
            run_id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL DEFAULT '',
            provider TEXT NOT NULL,
            model TEXT NOT NULL,
            api_style TEXT NOT NULL CHECK(api_style IN ('chat', 'responses')),
            status TEXT NOT NULL CHECK(status IN ('running', 'completed', 'error')),
            model_calls INTEGER NOT NULL DEFAULT 0,
            executed_steps INTEGER NOT NULL DEFAULT 0,
            state TEXT NOT NULL DEFAULT '{}',
            error_message TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_agent_model_calls_run
            ON agent_model_calls(run_id, call_index);
        CREATE INDEX IF NOT EXISTS idx_agent_checkpoints_status
            ON agent_checkpoints(status, updated_at);
        """
    )
    ensure_column(conn, "agent_model_calls", "duration_ms", "INTEGER NOT NULL DEFAULT 0")


def _pricing_for(model: str, pricing: Mapping[str, Any] | None) -> Mapping[str, Any]:
    if not pricing:
        return {}
    value = pricing.get(model)
    if isinstance(value, Mapping):
        return value
    value = pricing.get("*")
    return value if isinstance(value, Mapping) else {}


def estimate_usage_cost(
    usage: ModelUsage,
    pricing: Mapping[str, Any] | None,
) -> float | None:
    if usage.reported_cost is not None:
        return round(usage.reported_cost, 8)
    rates = _pricing_for(usage.model, pricing)
    if not rates:
        return None
    try:
        input_rate = float(rates.get("input_per_million", 0) or 0)
        output_rate = float(rates.get("output_per_million", 0) or 0)
        cached_rate = float(rates.get("cached_input_per_million", input_rate) or 0)
    except (TypeError, ValueError):
        return None
    uncached_input = max(0, usage.input_tokens - usage.cached_tokens)
    cost = (
        uncached_input * input_rate
        + usage.cached_tokens * cached_rate
        + usage.output_tokens * output_rate
    ) / 1_000_000
    return round(cost, 8)


class SQLiteAgentObserver:
    def __init__(
        self,
        conn: sqlite3.Connection,
        *,
        run_id: str,
        session_id: str,
        provider: str,
        model: str,
        api_style: str,
        pricing: Mapping[str, Any] | None = None,
    ) -> None:
        self.conn = conn
        self.run_id = run_id[:80]
        self.session_id = session_id[:80]
        self.provider = provider[:40]
        self.model = model[:128]
        self.api_style = api_style
        self.pricing = pricing
        row = conn.execute(
            "SELECT COALESCE(MAX(call_index), 0) AS value FROM agent_model_calls WHERE run_id = ?",
            (self.run_id,),
        ).fetchone()
        self.call_index = int(row["value"] if row is not None else 0)

    def record_usage(self, usage: ModelUsage) -> None:
        self.call_index += 1
        self.conn.execute(
            """
            INSERT INTO agent_model_calls
                (run_id, call_index, provider, model, api_style, purpose, input_tokens,
                 output_tokens, total_tokens, cached_tokens, reasoning_tokens,
                 duration_ms, estimated_cost, response_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id, call_index) DO UPDATE SET
                input_tokens = excluded.input_tokens,
                output_tokens = excluded.output_tokens,
                total_tokens = excluded.total_tokens,
                cached_tokens = excluded.cached_tokens,
                reasoning_tokens = excluded.reasoning_tokens,
                duration_ms = excluded.duration_ms,
                estimated_cost = excluded.estimated_cost,
                response_id = excluded.response_id
            """,
            (
                self.run_id,
                self.call_index,
                self.provider,
                usage.model[:128] or self.model,
                usage.api_style,
                usage.purpose[:40],
                usage.input_tokens,
                usage.output_tokens,
                usage.total_tokens,
                usage.cached_tokens,
                usage.reasoning_tokens,
                max(0, int(usage.duration_ms)),
                estimate_usage_cost(usage, self.pricing),
                usage.response_id[:120],
                now_iso(),
            ),
        )
        self.conn.commit()

    def save_checkpoint(self, state: dict[str, Any]) -> None:
        now = now_iso()
        self.conn.execute(
            """
            INSERT INTO agent_checkpoints
                (run_id, session_id, provider, model, api_style, status, model_calls,
                 executed_steps, state, error_message, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 'running', ?, ?, ?, '', ?, ?)
            ON CONFLICT(run_id) DO UPDATE SET
                status = 'running',
                model_calls = excluded.model_calls,
                executed_steps = excluded.executed_steps,
                state = excluded.state,
                error_message = '',
                updated_at = excluded.updated_at
            """,
            (
                self.run_id,
                self.session_id,
                self.provider,
                self.model,
                self.api_style,
                max(0, int(state.get("model_calls") or 0)),
                max(0, int(state.get("executed_steps") or 0)),
                json.dumps(state, ensure_ascii=False),
                now,
                now,
            ),
        )
        self.conn.commit()

    def load_checkpoint(self) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM agent_checkpoints WHERE run_id = ? AND status = 'running'",
            (self.run_id,),
        ).fetchone()
        if row is None:
            return None
        try:
            state = json.loads(row["state"])
        except json.JSONDecodeError:
            return None
        return state if isinstance(state, dict) else None

    def complete_checkpoint(self) -> None:
        self._finish_checkpoint("completed", "")

    def fail_checkpoint(self, error: Exception) -> None:
        self._finish_checkpoint("error", redact_sensitive_text(str(error))[:300])

    def _finish_checkpoint(self, status: str, error_message: str) -> None:
        self.conn.execute(
            """
            UPDATE agent_checkpoints
            SET status = ?, state = '{}', error_message = ?, updated_at = ?
            WHERE run_id = ?
            """,
            (status, error_message, now_iso(), self.run_id),
        )
        self.conn.commit()


def log_agent_run(
    conn: sqlite3.Connection,
    *,
    run_id: str = "",
    tool_mode: str = "native",
    session_id: str,
    provider: str,
    model: str,
    action: str,
    status: str,
    duration_ms: int,
    input_chars: int,
    output_count: int = 0,
    error: Exception | None = None,
) -> int:
    if status not in {"success", "error"}:
        raise ValueError("Agent 日志状态必须是 success 或 error")
    usage = conn.execute(
        """
        SELECT COUNT(*) AS requests,
               COALESCE(SUM(input_tokens), 0) AS input_tokens,
               COALESCE(SUM(output_tokens), 0) AS output_tokens,
               COALESCE(SUM(total_tokens), 0) AS total_tokens,
               COALESCE(SUM(cached_tokens), 0) AS cached_tokens,
               COALESCE(SUM(reasoning_tokens), 0) AS reasoning_tokens,
               SUM(estimated_cost) AS estimated_cost
        FROM agent_model_calls WHERE run_id = ?
        """,
        (run_id,),
    ).fetchone()
    error_type = type(error).__name__ if error is not None else ""
    error_message = redact_sensitive_text(str(error))[:300] if error is not None else ""
    cursor = conn.execute(
        """
        INSERT INTO agent_runs
            (run_id, tool_mode, session_id, provider, model, action, status, duration_ms,
             input_chars, output_count, error_type, error_message, model_requests,
             input_tokens, output_tokens, total_tokens, cached_tokens, reasoning_tokens,
             estimated_cost, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id[:80], tool_mode[:20], session_id[:80], provider[:40], model[:128],
            action[:40], status, max(0, int(duration_ms)), max(0, int(input_chars)),
            max(0, int(output_count)), error_type[:80], error_message,
            int(usage["requests"] or 0), int(usage["input_tokens"] or 0),
            int(usage["output_tokens"] or 0), int(usage["total_tokens"] or 0),
            int(usage["cached_tokens"] or 0), int(usage["reasoning_tokens"] or 0),
            usage["estimated_cost"], now_iso(),
        ),
    )
    conn.commit()
    return int(cursor.lastrowid)


def log_agent_step(conn: sqlite3.Connection, step: ToolStep) -> int:
    cursor = conn.execute(
        """
        INSERT INTO agent_steps
            (run_id, session_id, step_index, tool_call_id, tool_name, risk, status,
             duration_ms, error_type, error_message, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(run_id, step_index) DO UPDATE SET
            tool_call_id = excluded.tool_call_id,
            tool_name = excluded.tool_name,
            risk = excluded.risk,
            status = excluded.status,
            duration_ms = excluded.duration_ms,
            error_type = excluded.error_type,
            error_message = excluded.error_message,
            created_at = excluded.created_at
        """,
        (
            step.run_id[:80], step.session_id[:80], step.step_index, step.call_id[:80],
            step.tool_name[:80], step.risk.value, step.status,
            max(0, int(step.duration_ms)), step.error_type[:80],
            redact_sensitive_text(step.error_message)[:300], now_iso(),
        ),
    )
    conn.commit()
    return int(cursor.lastrowid)


def list_agent_runs(
    conn: sqlite3.Connection,
    limit: int = 100,
    status: str = "",
) -> list[dict[str, Any]]:
    limit = max(1, min(int(limit), 200))
    if status and status not in {"success", "error"}:
        raise ValueError("status 必须是 success 或 error")
    if status:
        rows = conn.execute(
            "SELECT * FROM agent_runs WHERE status = ? ORDER BY id DESC LIMIT ?",
            (status, limit),
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM agent_runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    runs = [dict(row) for row in rows]
    run_ids = [item["run_id"] for item in runs if item.get("run_id")]
    steps_by_run: dict[str, list[dict[str, Any]]] = {run_id: [] for run_id in run_ids}
    calls_by_run: dict[str, list[dict[str, Any]]] = {run_id: [] for run_id in run_ids}
    if run_ids:
        placeholders = ",".join("?" for _ in run_ids)
        for row in conn.execute(
            f"SELECT * FROM agent_steps WHERE run_id IN ({placeholders}) ORDER BY run_id, step_index",
            run_ids,
        ):
            steps_by_run[row["run_id"]].append(dict(row))
        for row in conn.execute(
            f"SELECT * FROM agent_model_calls WHERE run_id IN ({placeholders}) ORDER BY run_id, call_index",
            run_ids,
        ):
            calls_by_run[row["run_id"]].append(dict(row))
    for item in runs:
        item["steps"] = steps_by_run.get(item.get("run_id", ""), [])
        item["model_calls"] = calls_by_run.get(item.get("run_id", ""), [])
    return runs
