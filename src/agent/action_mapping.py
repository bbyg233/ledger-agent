from __future__ import annotations

from typing import Any

from agent.contracts import ToolCall
from agent.models import AgentAction


def model_safe_tool_output(tool_name: str, output: dict[str, Any]) -> dict[str, Any]:
    if tool_name == "search_ledger":
        safe_fields = {
            "id", "date", "amount", "direction", "category", "account", "merchant",
            "statement_month", "record_type", "liability_id",
        }
        return {
            "results": [
                {key: value for key, value in item.items() if key in safe_fields}
                for item in output.get("results", [])
                if isinstance(item, dict)
            ]
        }
    if tool_name == "get_subscriptions":
        data = output.get("subscriptions") if isinstance(output.get("subscriptions"), dict) else {}
        safe_fields = {
            "name", "amount", "cycle_months", "next_charge_date", "category", "account",
            "is_active", "charge_status", "last_charge_date", "charge_count",
        }
        return {
            "subscriptions": {
                "month": data.get("month"),
                "summary": data.get("summary", {}),
                "items": [
                    {key: value for key, value in item.items() if key in safe_fields}
                    for item in data.get("items", [])
                    if isinstance(item, dict)
                ],
            }
        }
    if tool_name == "get_liabilities":
        data = output.get("liabilities") if isinstance(output.get("liabilities"), dict) else {}
        safe_fields = {
            "id", "name", "provider", "kind", "statement_month", "due_amount", "remaining_amount", "due_date",
            "paid_amount", "payment_count", "minimum_payment", "repayment_account", "credit_limit", "is_active", "payment_status",
            "is_carried_forward", "carried_from_month",
        }
        return {
            "liabilities": {
                "month": data.get("month"),
                "summary": data.get("summary", {}),
                "items": [
                    {key: value for key, value in item.items() if key in safe_fields}
                    for item in data.get("items", [])
                    if isinstance(item, dict)
                ],
            }
        }
    if tool_name == "get_account_balances":
        data = output.get("accounts") if isinstance(output.get("accounts"), dict) else {}
        return {
            "accounts": {
                "total_balance": data.get("total_balance", 0),
                "items": [
                    {
                        key: item.get(key)
                        for key in ("name", "kind", "balance", "last_difference", "baseline_date")
                    }
                    for item in data.get("items", [])
                    if isinstance(item, dict)
                ],
            }
        }
    return output


def tool_call_agent_action(
    call: ToolCall,
    text: str,
    result: dict[str, Any],
) -> AgentAction:
    arguments = call.arguments
    if call.name == "ask_clarification":
        return AgentAction(action="clarify", text=text, question=str(result.get("question") or ""))
    if call.name == "record_transactions":
        drafts = result.get("drafts") or ([result["draft"]] if result.get("draft") else [])
        return AgentAction(
            action="record",
            text=text,
            transaction=drafts[0] if len(drafts) == 1 else None,
            transactions=drafts,
        )
    if call.name == "get_month_summary":
        return AgentAction(action="summary", text=text, month=str(arguments.get("month") or ""))
    if call.name == "create_budget_plan":
        return AgentAction(
            action="plan",
            text=text,
            month=str(arguments.get("month") or ""),
            monthly_income=arguments.get("monthly_income"),
            saving_goal=float(arguments.get("saving_goal") or 0),
        )
    if call.name == "search_ledger":
        return AgentAction(
            action="search",
            text=text,
            month=str(arguments.get("month") or ""),
            query=str(arguments.get("query") or ""),
            category=str(arguments.get("category") or ""),
            account=str(arguments.get("account") or ""),
            direction=str(arguments.get("direction") or ""),
            min_amount=arguments.get("min_amount"),
            max_amount=arguments.get("max_amount"),
            limit=int(arguments.get("limit") or 20),
        )
    if call.name == "aggregate_spending":
        return AgentAction(
            action="where",
            text=text,
            month=str(arguments.get("month") or ""),
            query=str(arguments.get("query") or ""),
            group_by=str(arguments.get("group_by") or "category"),
            limit=int(arguments.get("limit") or 20),
        )
    if call.name == "analyze_spending_trend":
        return AgentAction(
            action="analyze",
            text=text,
            month=str(arguments.get("end_month") or ""),
            category=str(arguments.get("category") or ""),
            periods=int(arguments.get("periods") or 3),
        )
    if call.name == "compare_spending_periods":
        return AgentAction(
            action="compare",
            text=text,
            current_start=str(arguments.get("current_start") or ""),
            current_end=str(arguments.get("current_end") or ""),
            baseline_start=str(arguments.get("baseline_start") or ""),
            baseline_end=str(arguments.get("baseline_end") or ""),
            category=str(arguments.get("category") or ""),
        )
    if call.name == "find_recurring_expenses":
        return AgentAction(
            action="recurring",
            text=text,
            month=str(arguments.get("end_month") or ""),
            recurring_months=int(arguments.get("months") or 6),
            min_occurrences=int(arguments.get("min_occurrences") or 3),
            min_amount=float(arguments.get("min_amount") or 0),
        )
    if call.name == "get_subscriptions":
        return AgentAction(action="subscriptions", text=text, month=str(arguments.get("month") or ""))
    if call.name == "get_liabilities":
        return AgentAction(action="liabilities", text=text, month=str(arguments.get("month") or ""))
    if call.name == "get_account_balances":
        return AgentAction(action="accounts", text=text)
    if call.name in {
        "propose_subscriptions",
        "propose_subscription_charge",
        "propose_subscription_skip",
        "propose_liability_statement",
        "propose_liability_payment",
        "propose_liability_charge",
        "propose_account_transfer",
    }:
        return AgentAction(action="management", text=text)
    if call.name == "generate_monthly_report":
        return AgentAction(action="report", text=text, month=str(arguments.get("month") or ""))
    raise ValueError(f"无法把工具映射为 Agent 动作: {call.name}")
