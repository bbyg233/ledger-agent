from __future__ import annotations

import json
import re
import sqlite3
from datetime import date
from typing import Any

from agent.models import AgentAction, AgentContext
from financial_agent import (
    current_month,
    ensure_session,
    list_accounts,
    list_liabilities,
    list_merchant_category_rules,
    list_reference_values,
    list_subscriptions,
    merchant_rule_key,
    now_iso,
    recent_messages,
)


def set_preference(conn: sqlite3.Connection, key: str, value: Any) -> None:
    if not key:
        raise ValueError("preference key 不能为空")
    serialized = json.dumps(value, ensure_ascii=False)
    conn.execute(
        """
        INSERT INTO preferences (key, value, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
        """,
        (key, serialized, now_iso()),
    )
    conn.commit()


def get_preferences(conn: sqlite3.Connection) -> dict[str, Any]:
    preferences: dict[str, Any] = {}
    for row in conn.execute("SELECT key, value FROM preferences ORDER BY key"):
        try:
            preferences[row["key"]] = json.loads(row["value"])
        except json.JSONDecodeError:
            preferences[row["key"]] = row["value"]
    return preferences


def get_agent_state(conn: sqlite3.Connection, session_id: str) -> dict[str, Any]:
    ensure_session(conn, session_id)
    row = conn.execute("SELECT * FROM agent_state WHERE session_id = ?", (session_id,)).fetchone()
    if row is None:
        return {
            "current_month": current_month(),
            "last_action": "",
            "last_focus": "",
            "last_result": {},
        }
    try:
        last_result = json.loads(row["last_result"])
    except json.JSONDecodeError:
        last_result = {}
    return {
        "current_month": row["current_month"],
        "last_action": row["last_action"],
        "last_focus": row["last_focus"],
        "last_result": last_result,
    }


def save_agent_state(conn: sqlite3.Connection, session_id: str, action: AgentAction, result: dict[str, Any]) -> None:
    ensure_session(conn, session_id)
    focus = action.category or action.query or action.month or action.group_by or ""
    state_month = action.month or current_month()
    compact_result = json.dumps(result, ensure_ascii=False)[:4000]
    conn.execute(
        """
        INSERT INTO agent_state (session_id, current_month, last_action, last_focus, last_result, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(session_id) DO UPDATE SET
            current_month = excluded.current_month,
            last_action = excluded.last_action,
            last_focus = excluded.last_focus,
            last_result = excluded.last_result,
            updated_at = excluded.updated_at
        """,
        (session_id, state_month, action.action, focus, compact_result, now_iso()),
    )
    conn.commit()


def load_agent_context(conn: sqlite3.Connection, session_id: str, message_limit: int = 10) -> AgentContext:
    ensure_session(conn, session_id)
    return AgentContext(
        session_id=session_id,
        today=date.today().isoformat(),
        current_month=current_month(),
        recent_messages=recent_messages(conn, session_id, limit=message_limit),
        preferences=get_preferences(conn),
        state=get_agent_state(conn, session_id),
        category_catalog=[
            {
                "name": item["name"],
                "aliases": item["aliases"],
                "is_favorite": item["is_favorite"],
            }
            for item in list_reference_values(conn, "category")
        ],
        merchant_rules=list_merchant_category_rules(conn),
        subscription_catalog=[
            {
                "id": item["id"], "name": item["name"], "amount": item["amount"],
                "cycle_months": item["cycle_months"], "next_charge_date": item["next_charge_date"],
            }
            for item in list_subscriptions(conn, include_inactive=False)["items"][:40]
        ],
        liability_catalog=[
            {
                "id": item["id"], "name": item["name"], "provider": item["provider"], "kind": item["kind"],
                "statement_month": item["statement_month"], "due_amount": item["due_amount"],
                "remaining_amount": item["remaining_amount"],
                "due_date": item["due_date"], "minimum_payment": item["minimum_payment"],
            }
            for item in list_liabilities(
                conn, include_inactive=False, include_without_statement=True
            )["items"][:40]
        ],
        account_catalog=[
            {"name": item["name"], "kind": item["kind"], "balance": item["balance"]}
            for item in list_accounts(conn)["items"]
        ],
    )


def context_for_prompt(context: AgentContext, current_text: str = "") -> str:
    """Build the minimum model context needed to resolve the current message.

    The SQLite ledger remains available through tools. Keeping large historical
    tool results out of this prompt substantially reduces every model request.
    """
    recent_user_text = " ".join(
        str(message.get("content") or "")
        for message in context.recent_messages[-6:]
        if message.get("role") == "user"
    )
    searchable_text = re.sub(r"\s+", "", f"{recent_user_text} {current_text}").casefold()

    def matching_catalog(items: list[dict[str, Any]], fields: tuple[str, ...]) -> list[dict[str, Any]]:
        return [
            item for item in items
            if any(
                str(item.get(field) or "").replace(" ", "").casefold() in searchable_text
                for field in fields
                if str(item.get(field) or "").strip()
            )
        ][:8]

    relevant_rules = [
        rule
        for rule in context.merchant_rules
        if merchant_rule_key(rule.get("merchant_display", "")) in searchable_text
    ][:10]
    recent_messages = []
    for message in context.recent_messages[-4:]:
        content = str(message.get("content") or "").strip()
        if message.get("role") == "assistant":
            try:
                data = json.loads(content)
                content = f"已处理：{data.get('kind', 'result')} / {data.get('agent_action', {}).get('action', '')}"
            except (TypeError, ValueError):
                content = content[:240]
        recent_messages.append({"role": message.get("role"), "content": content[:500]})
    public_context = {
        "today": context.today,
        "current_month": context.current_month,
        "recent_messages": recent_messages,
        "preferences": {
            key: value for key, value in context.preferences.items()
            if key in {"default_account", "default_category"}
        },
        "state": {
            key: context.state.get(key, "")
            for key in ("current_month", "last_action", "last_focus")
        },
        "category_catalog": [
            {"name": item["name"], "aliases": item.get("aliases", [])}
            for item in context.category_catalog[:30]
        ],
        "merchant_category_rules": relevant_rules,
        "subscription_catalog": matching_catalog(context.subscription_catalog, ("name",)),
        "liability_catalog": matching_catalog(context.liability_catalog, ("name", "provider")),
        "account_catalog": context.account_catalog[:20],
    }
    return json.dumps(public_context, ensure_ascii=False, separators=(",", ":"))
