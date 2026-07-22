from __future__ import annotations

import re
import sqlite3
from dataclasses import asdict, dataclass
from typing import Any

import financial_agent as core
from agent import AgentRunPolicy, AgentRunner, NativeToolCallingError, ToolExecutionContext
from agent.action_mapping import model_safe_tool_output, tool_call_agent_action
from agent.models import AgentAction, AgentContext
from agent.prompts import native_agent_system_prompt
from agent.usage import observe_model_usage
from ledger.observability import SQLiteAgentObserver, log_agent_step, redact_sensitive_text
@dataclass(frozen=True)
class NativeToolPlan:
    profile: str
    tool_names: tuple[str, ...]


def native_agent_tool_plan(text: str, *, has_images: bool = False) -> NativeToolPlan:
    """Select a conservative tool subset for a clearly expressed local task."""
    all_tools = core.LEDGER_TOOL_REGISTRY.names()
    if has_images:
        return NativeToolPlan("general", all_tools)
    compact = re.sub(r"\s+", "", text).casefold()
    credit_terms = ("花呗", "白条", "月付", "信用卡", "分期", "待还", "欠款")
    query_terms = ("查询", "搜索", "查找", "汇总", "统计", "分析", "为什么", "趋势", "对比", "比较", "报告", "复盘")
    if any(term in compact for term in credit_terms):
        return NativeToolPlan(
            "credit",
            (
                "ask_clarification", "get_liabilities", "propose_liability_charge",
                "propose_liability_payment", "propose_liability_statement",
            ),
        )
    if "订阅" in compact or any(
        term in compact for term in ("会员", "自动扣款", "自动续费", "续费")
    ):
        return NativeToolPlan(
            "general",
            (
                "ask_clarification", "get_subscriptions", "propose_subscriptions",
                "propose_subscription_charge", "propose_subscription_skip",
            ),
        )
    if "转账" in compact or ("转" in compact and "到" in compact and "元" in compact):
        return NativeToolPlan(
            "transfer", ("ask_clarification", "get_account_balances", "propose_account_transfer"),
        )
    if any(term in compact for term in ("钱花在哪", "花在哪", "支出分布")):
        return NativeToolPlan("general", ("aggregate_spending",))
    if any(term in compact for term in ("为什么", "趋势", "变多", "变少")):
        return NativeToolPlan("general", ("analyze_spending_trend", "aggregate_spending"))
    if any(term in compact for term in ("对比", "比较")):
        return NativeToolPlan("general", ("compare_spending_periods", "ask_clarification"))
    if any(term in compact for term in ("周期", "规律", "固定支出")):
        return NativeToolPlan("general", ("find_recurring_expenses",))
    if any(term in compact for term in ("预算", "存钱规划")):
        return NativeToolPlan("general", ("get_month_summary", "create_budget_plan", "ask_clarification"))
    if any(term in compact for term in ("报告", "复盘")):
        return NativeToolPlan("general", ("generate_monthly_report",))
    if any(term in compact for term in ("搜索", "查找", "明细", "哪笔")):
        return NativeToolPlan("general", ("search_ledger",))
    if any(term in compact for term in ("汇总", "统计", "花了多少", "结余", "收入多少", "支出多少")):
        return NativeToolPlan("general", ("get_month_summary", "aggregate_spending"))
    if any(term in compact for term in query_terms):
        return NativeToolPlan("general", all_tools)
    if any(term in compact for term in ("分类", "算什么", "是什么", "怎么做", "解释")):
        return NativeToolPlan("general", all_tools)
    if re.search(r"(?:¥|￥|\d+(?:\.\d+)?\s*(?:元|块))", compact):
        return NativeToolPlan("quick_ledger", ("ask_clarification", "record_transactions"))
    return NativeToolPlan("general", all_tools)


def build_agent_runner(conn: sqlite3.Connection, registry=None) -> AgentRunner:
    registry = registry or core.LEDGER_TOOL_REGISTRY
    policy = AgentRunPolicy(
        max_steps=5,
        max_duration_seconds=float(core.env_float("LEDGER_AGENT_RUN_TIMEOUT_SECONDS", 120) or 120),
        allowed_tools=frozenset(registry.names()),
    )
    return AgentRunner(
        registry,
        policy=policy,
        step_hook=lambda step: log_agent_step(conn, step),
        redact=redact_sensitive_text,
    )


def execute_native_agent(
    conn: sqlite3.Connection,
    text: str,
    context: AgentContext,
    *,
    run_id: str,
    session_id: str,
    dry_run: bool,
    assume_yes: bool,
    allow_interactive_approval: bool,
    image_data_urls: tuple[str, ...] = (),
) -> tuple[AgentAction, dict[str, Any]]:
    provider = core.llm_provider()
    model = core.llm_model(provider)
    responses_api = core.llm_uses_responses_api(model)
    observer = SQLiteAgentObserver(
        conn,
        run_id=run_id,
        session_id=session_id,
        provider=provider,
        model=model,
        api_style="responses" if responses_api else "chat",
        pricing=core.model_pricing(),
    )
    plan = native_agent_tool_plan(text, has_images=bool(image_data_urls))
    registry = core.LEDGER_TOOL_REGISTRY.select(plan.tool_names)
    runner = build_agent_runner(conn, registry)
    execution_context = ToolExecutionContext(
        state=conn,
        run_id=run_id,
        session_id=session_id,
        dry_run=dry_run,
        approval_granted=assume_yes,
        allow_interactive_approval=allow_interactive_approval,
        metadata={"native_mode": True, "user_text": text, "tool_profile": plan.profile},
    )
    loop = core.NativeToolLoop(
        client=core.llm_client(),
        model=model,
        registry=registry,
        runner=runner,
        context=execution_context,
        safe_output=model_safe_tool_output,
        max_model_calls=6,
    )
    user_prompt = f"上下文:\n{core.context_for_prompt(context, text)}\n\n用户当前输入: {text}"
    try:
        with observe_model_usage(observer.record_usage):
            outcome = loop.run(
                native_agent_system_prompt(plan.profile),
                user_prompt,
                responses_api=responses_api,
                image_data_urls=image_data_urls,
                checkpoint_state=None if image_data_urls else observer.load_checkpoint(),
                checkpoint_hook=None if image_data_urls else observer.save_checkpoint,
            )
        observer.complete_checkpoint()
    except Exception as exc:
        observer.fail_checkpoint(exc)
        raise
    if not outcome.calls or not outcome.results:
        raise NativeToolCallingError("原生模式没有产生可执行工具")
    selected_index = -1 if outcome.stopped_for_confirmation else 0
    selected_call = outcome.calls[selected_index]
    selected_output = outcome.results[selected_index].output
    action = tool_call_agent_action(selected_call, text, selected_output)
    if action.action in {"record", "clarify", "management"}:
        return action, {"agent_action": asdict(action), **selected_output}
    return action, {
        "agent_action": asdict(action),
        **outcome.results[0].output,
        "native_answer": outcome.final_text,
        "tools_used": [call.name for call in outcome.calls],
        "tool_outputs": [result.output for result in outcome.results],
    }


def execute_agent_request(
    conn: sqlite3.Connection,
    text: str,
    context: AgentContext,
    *,
    run_id: str,
    session_id: str,
    preview_writes: bool,
    assume_yes: bool = False,
    allow_interactive_approval: bool = False,
    image_data_urls: tuple[str, ...] = (),
) -> tuple[AgentAction, dict[str, Any]]:
    action, result = core.execute_native_agent(
        conn,
        text,
        context,
        run_id=run_id,
        session_id=session_id,
        dry_run=preview_writes,
        assume_yes=assume_yes,
        allow_interactive_approval=allow_interactive_approval,
        image_data_urls=image_data_urls,
    )
    return action, result


def agent_tool_catalog() -> list[dict[str, Any]]:
    return core.LEDGER_TOOL_REGISTRY.model_schemas()
