from __future__ import annotations

from dataclasses import asdict
from datetime import date
from typing import Any

import financial_agent as core
import sqlite3
from agent import ToolExecutionContext
from agent.ledger_tools import (
    AggregateInput,
    AccountQueryInput,
    AccountTransferProposalInput,
    ClarificationInput,
    DailyReminderInput,
    LiabilityChargeProposalInput,
    LiabilityPaymentProposalInput,
    LiabilityQueryInput,
    LiabilityStatementProposalInput,
    MonthInput,
    PeriodComparisonInput,
    PlanInput,
    RecordTransactionsInput,
    RecurringExpenseInput,
    SearchInput,
    SpendingTrendInput,
    SubscriptionChargeProposalInput,
    SubscriptionProposalInput,
    SubscriptionQueryInput,
    SubscriptionSkipProposalInput,
    RememberPersonalPreferenceInput,
    build_ledger_tool_registry,
)
from services.reminder import (
    REMINDER_PREFERENCE_KEY,
    reminder_view,
    set_reminder_skip_for_today,
    update_reminder_settings,
)
from services.reminder_scheduler import schedule_windows_reminder_sync
from services.personal_memory import (
    PERSONAL_MEMORY_PREFERENCE_KEY,
    create_personal_memory,
)


def create_transactions_from_drafts(
    conn: sqlite3.Connection,
    transactions: list[dict[str, Any]],
    dry_run: bool,
    assume_yes: bool,
) -> dict[str, Any]:
    if not transactions:
        raise ValueError("至少需要一条账单草稿")
    if len(transactions) > 20:
        raise ValueError("单次最多处理 20 条账单草稿")
    drafts = [
        core.validate_transaction_payload(
            transaction,
            raw_text=str(transaction.get("raw_text") or ""),
        )
        for transaction in transactions
    ]
    for draft in drafts:
        core.normalize_draft(conn, draft)
    public_drafts = [asdict(draft) for draft in drafts]
    if dry_run:
        return {"drafts": public_drafts, "written": 0}
    if not core.confirm_writes(drafts, assume_yes=assume_yes):
        return {"drafts": public_drafts, "written": 0, "cancelled": True}
    result = core.add_transactions(conn, drafts)
    return {**result, "transactions": public_drafts}


def _tool_ask_clarification(
    payload: ClarificationInput, context: ToolExecutionContext
) -> dict[str, Any]:
    return {"question": payload.question, "requires_input": True}


def _tool_record_transactions(
    payload: RecordTransactionsInput, context: ToolExecutionContext
) -> dict[str, Any]:
    conn = context.state
    assume_yes = context.approval_granted
    raw_text = str(context.metadata.get("user_text") or "")
    transactions = []
    for item in payload.transactions:
        transaction = item.model_dump()
        transaction["raw_text"] = raw_text
        transactions.append(transaction)
    return create_transactions_from_drafts(
        conn,
        transactions,
        dry_run=context.dry_run,
        assume_yes=assume_yes,
    )


def _tool_month_summary(payload: MonthInput, context: ToolExecutionContext) -> dict[str, Any]:
    return {"summary": core.summarize(context.state, core.normalize_month(payload.month))}


def _tool_budget_plan(payload: PlanInput, context: ToolExecutionContext) -> dict[str, Any]:
    month = core.normalize_month(payload.month)
    summary = core.summarize(context.state, month)
    return {
        "summary": summary,
        "advice": core.planning_advice(summary, payload.monthly_income, payload.saving_goal),
    }


def _tool_search_ledger(payload: SearchInput, context: ToolExecutionContext) -> dict[str, Any]:
    return {
        "results": core.recent_financial_records(
            context.state,
            query=payload.query,
            month=payload.month,
            category=payload.category,
            account=payload.account,
            direction=payload.direction,
            min_amount=payload.min_amount,
            max_amount=payload.max_amount,
            limit=payload.limit,
        )
    }


def _tool_aggregate_spending(
    payload: AggregateInput, context: ToolExecutionContext
) -> dict[str, Any]:
    return {
        "where": core.where_money_went(
            context.state,
            month=payload.month,
            query=payload.query,
            group_by=payload.group_by,
            limit=payload.limit,
        )
    }


def _tool_analyze_spending(
    payload: SpendingTrendInput, context: ToolExecutionContext
) -> dict[str, Any]:
    analysis = core.spending_trend_analysis(
        context.state,
        end_month=core.normalize_month(payload.end_month),
        category=payload.category,
        periods=payload.periods,
    )
    if context.metadata.get("native_mode"):
        return {"analysis": analysis}
    return {"analysis": analysis, "narrative": core.narrate_spending_analysis(analysis)}


def _tool_compare_spending(
    payload: PeriodComparisonInput, context: ToolExecutionContext
) -> dict[str, Any]:
    return {
        "comparison": core.compare_spending_periods(
            context.state,
            current_start=payload.current_start,
            current_end=payload.current_end,
            baseline_start=payload.baseline_start,
            baseline_end=payload.baseline_end,
            category=payload.category,
        )
    }


def _tool_recurring_expenses(
    payload: RecurringExpenseInput, context: ToolExecutionContext
) -> dict[str, Any]:
    return {
        "recurring": core.find_recurring_expenses(
            context.state,
            end_month=payload.end_month,
            months=payload.months,
            min_occurrences=payload.min_occurrences,
            min_amount=payload.min_amount,
        )
    }


def _tool_subscriptions(
    payload: SubscriptionQueryInput, context: ToolExecutionContext
) -> dict[str, Any]:
    return {
        "subscriptions": core.list_subscriptions(
            context.state,
            month=payload.month,
            include_inactive=payload.include_inactive,
        )
    }


def _tool_liabilities(
    payload: LiabilityQueryInput, context: ToolExecutionContext
) -> dict[str, Any]:
    return {
        "liabilities": core.list_liabilities(
            context.state,
            month=payload.month,
            include_inactive=payload.include_inactive,
        )
    }


def _tool_account_balances(
    payload: AccountQueryInput, context: ToolExecutionContext
) -> dict[str, Any]:
    return {"accounts": core.list_accounts(context.state, include_inactive=payload.include_inactive)}


def _tool_propose_subscriptions(
    payload: SubscriptionProposalInput, context: ToolExecutionContext
) -> dict[str, Any]:
    proposals = []
    for item in payload.subscriptions:
        draft = core._normalize_subscription_payload(context.state, item.model_dump())
        proposals.append({"type": "subscription_create", "draft": draft})
    return {"proposals": proposals}


def _tool_propose_subscription_charge(
    payload: SubscriptionChargeProposalInput, context: ToolExecutionContext
) -> dict[str, Any]:
    subscription = core.get_subscription(context.state, payload.subscription_id)
    if not subscription["is_active"]:
        raise ValueError("已停用的订阅不能登记扣款")
    if subscription["next_charge_date"] > date.today().isoformat():
        raise ValueError("未来扣款尚未发生，不能确认已扣款；请先核对下次扣款日")
    return {
        "proposals": [
            {
                "type": "subscription_charge",
                "subscription_id": subscription["id"],
                "draft": {
                    "name": subscription["name"], "amount": subscription["amount"],
                    "date": subscription["next_charge_date"], "category": subscription["category"],
                    "account": subscription["account"],
                },
            }
        ]
    }


def _tool_propose_subscription_skip(
    payload: SubscriptionSkipProposalInput, context: ToolExecutionContext
) -> dict[str, Any]:
    subscription = core.get_subscription(context.state, payload.subscription_id)
    if not subscription["is_active"]:
        raise ValueError("已停用的订阅不能跳过扣款")
    next_charge_date = core._add_months(
        date.fromisoformat(subscription["next_charge_date"]), int(subscription["cycle_months"])
    ).isoformat()
    return {
        "proposals": [
            {
                "type": "subscription_skip",
                "subscription_id": subscription["id"],
                "draft": {
                    "name": subscription["name"],
                    "skipped_date": subscription["next_charge_date"],
                    "next_charge_date": next_charge_date,
                },
            }
        ]
    }


def _tool_propose_liability_statement(
    payload: LiabilityStatementProposalInput, context: ToolExecutionContext
) -> dict[str, Any]:
    source = payload.model_dump(exclude_unset=True)
    liability_id = source.pop("liability_id", "")
    statement_month = source.pop("statement_month")
    amount_mode = str(source.pop("amount_mode", "add"))
    if not liability_id:
        match = context.state.execute(
            "SELECT id FROM liabilities WHERE is_active = 1 AND lower(name) = lower(?) LIMIT 1",
            (str(source.get("name") or "").strip(),),
        ).fetchone()
        if match is not None:
            liability_id = str(match["id"])
    if liability_id:
        existing = core.get_liability(context.state, liability_id)
        current_statement = core.get_liability_for_month(
            context.state, liability_id, statement_month
        )
        statement_defaults: dict[str, Any] = {}
        if current_statement["has_statement"]:
            statement_defaults = {
                "due_amount": current_statement["due_amount"],
                "due_date": current_statement["due_date"],
                "minimum_payment": current_statement["minimum_payment"],
            }
        if "due_date" not in source:
            statement_defaults["due_date"] = core.inherited_liability_due_date(
                context.state, liability_id, statement_month
            )
        if current_statement["has_statement"] and amount_mode == "add":
            source["due_amount"] = round(
                float(current_statement["due_amount"]) + float(source["due_amount"]), 2
            )
        draft = core._normalize_liability_payload(
            context.state, {**existing, **statement_defaults, **source}
        )
        proposal_type = "liability_update"
    else:
        draft = core._normalize_liability_payload(context.state, source)
        proposal_type = "liability_create"
    draft.pop("outstanding_balance", None)
    draft["statement_month"] = statement_month
    proposal: dict[str, Any] = {
        "type": proposal_type, "liability_id": liability_id, "draft": draft
    }
    if liability_id and current_statement["has_statement"] and amount_mode == "add":
        proposal["merged_amount"] = float(payload.due_amount)
        proposal["previous_due_amount"] = float(current_statement["due_amount"])
    return {
        "proposals": [proposal]
    }


def _tool_propose_liability_payment(
    payload: LiabilityPaymentProposalInput, context: ToolExecutionContext
) -> dict[str, Any]:
    account = core.get_liability(context.state, payload.liability_id)
    statement_month = payload.statement_month or str(account["due_date"])[:7]
    liability = core.get_liability_for_month(context.state, payload.liability_id, statement_month)
    if not liability["has_statement"]:
        raise ValueError("该月份没有待还账单，请先录入本月应还")
    amount = round(float(payload.amount), 2)
    if amount > float(liability["remaining_amount"]):
        raise ValueError("还款金额不能超过本月未还金额")
    return {
        "proposals": [
            {
                "type": "liability_payment",
                "liability_id": liability["id"],
                "draft": {
                    "statement_month": liability["statement_month"],
                    "amount": amount,
                    "paid_at": payload.paid_at,
                    "note": payload.note,
                },
            }
        ]
    }


def _tool_propose_liability_charge(
    payload: LiabilityChargeProposalInput, context: ToolExecutionContext
) -> dict[str, Any]:
    liabilities: dict[str, dict[str, Any]] = {}
    group_totals: dict[tuple[str, str], float] = {}
    group_existing_due: dict[tuple[str, str], float] = {}
    resolved_charges: list[tuple[Any, str]] = []
    for item in payload.charges:
        liability = liabilities.setdefault(item.liability_id, core.get_liability(context.state, item.liability_id))
        if not liability["is_active"]:
            raise ValueError("已停用的待还项目不能登记信用消费")
        statement_month = core.resolve_credit_charge_statement_month(
            context.state,
            item.liability_id,
            item.charged_at,
            item.statement_month,
        )
        resolved_charges.append((item, statement_month))
        key = (item.liability_id, statement_month)
        if key not in group_existing_due:
            statement = core.get_liability_for_month(context.state, item.liability_id, statement_month)
            group_existing_due[key] = float(statement["due_amount"])
        group_totals[key] = round(group_totals.get(key, 0) + float(item.amount), 2)

    proposals = []
    for item, statement_month in resolved_charges:
        key = (item.liability_id, statement_month)
        existing_due = group_existing_due[key]
        batch_amount = group_totals[key]
        proposal = {
            "type": "liability_charge",
            "liability_id": item.liability_id,
            "previous_due_amount": existing_due,
            "batch_charge_amount": batch_amount,
            "projected_due_amount": round(existing_due + batch_amount, 2),
            "draft": {
                "statement_month": statement_month,
                "amount": round(float(item.amount), 2),
                "charged_at": item.charged_at,
                "category": item.category,
                "merchant": item.merchant,
                "note": item.note,
                "liability_name": liabilities[item.liability_id]["name"],
            },
        }
        if statement_month != item.statement_month:
            proposal["statement_month_adjusted_from"] = item.statement_month
        proposals.append(proposal)
    return {"proposals": proposals}


def _tool_propose_account_transfer(
    payload: AccountTransferProposalInput, context: ToolExecutionContext
) -> dict[str, Any]:
    source = core._require_asset_account(context.state, payload.source_account)
    target = core._require_asset_account(context.state, payload.target_account)
    if source == target:
        raise ValueError("转出和转入账户不能相同")
    return {
        "proposals": [
            {
                "type": "account_transfer",
                "draft": {
                    "source_account": source,
                    "target_account": target,
                    "amount": round(float(payload.amount), 2),
                    "transferred_on": payload.transferred_on,
                    "note": payload.note,
                },
            }
        ]
    }


def _tool_manage_daily_reminder(
    payload: DailyReminderInput, context: ToolExecutionContext
) -> dict[str, Any]:
    settings = core.get_preferences(context.state).get(REMINDER_PREFERENCE_KEY, {})
    settings = update_reminder_settings(
        settings,
        enabled=payload.enabled,
        reminder_time=payload.time or None,
    )
    if payload.skip_today is not None:
        settings = set_reminder_skip_for_today(settings, skip=payload.skip_today)
    core.set_preference(context.state, REMINDER_PREFERENCE_KEY, settings)
    result = reminder_view(settings)
    core.audit(context.state, "settings.reminder.agent", result, source="agent")
    context.state.commit()
    if payload.enabled is not None or payload.time:
        result["scheduler_sync_scheduled"] = schedule_windows_reminder_sync(
            result["time"], enabled=result["enabled"]
        )
    return {"reminder": result}


def _tool_remember_personal_preference(
    payload: RememberPersonalPreferenceInput, context: ToolExecutionContext
) -> dict[str, Any]:
    preferences = core.get_preferences(context.state)
    memories, memory = create_personal_memory(
        preferences.get(PERSONAL_MEMORY_PREFERENCE_KEY, []),
        title=payload.title,
        content=payload.content,
        source="agent",
    )
    core.set_preference(context.state, PERSONAL_MEMORY_PREFERENCE_KEY, memories)
    core.audit(
        context.state,
        "settings.personal_memory.agent",
        {"id": memory["id"], "title": memory["title"]},
        source="agent",
    )
    context.state.commit()
    return {"memory": memory}


def _tool_monthly_report(payload: MonthInput, context: ToolExecutionContext) -> dict[str, Any]:
    report = core.monthly_report(context.state, core.normalize_month(payload.month))
    if context.metadata.get("native_mode"):
        return {"report": report}
    return {"report": report, "narrative": core.narrate_monthly_report(report)}

def build_tool_registry():
    return build_ledger_tool_registry(
        {
            "ask_clarification": _tool_ask_clarification,
            "record_transactions": _tool_record_transactions,
            "get_month_summary": _tool_month_summary,
            "create_budget_plan": _tool_budget_plan,
            "search_ledger": _tool_search_ledger,
            "aggregate_spending": _tool_aggregate_spending,
            "analyze_spending_trend": _tool_analyze_spending,
            "compare_spending_periods": _tool_compare_spending,
            "find_recurring_expenses": _tool_recurring_expenses,
            "get_subscriptions": _tool_subscriptions,
            "get_liabilities": _tool_liabilities,
            "get_account_balances": _tool_account_balances,
            "propose_subscriptions": _tool_propose_subscriptions,
            "propose_subscription_charge": _tool_propose_subscription_charge,
            "propose_subscription_skip": _tool_propose_subscription_skip,
            "propose_liability_statement": _tool_propose_liability_statement,
            "propose_liability_payment": _tool_propose_liability_payment,
            "propose_liability_charge": _tool_propose_liability_charge,
            "propose_account_transfer": _tool_propose_account_transfer,
            "manage_daily_reminder": _tool_manage_daily_reminder,
            "remember_personal_preference": _tool_remember_personal_preference,
            "generate_monthly_report": _tool_monthly_report,
        }
    )
