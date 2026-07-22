from __future__ import annotations

from collections.abc import Callable, Iterable
from time import perf_counter

from agent.contracts import (
    AgentRunPolicy,
    ToolCall,
    ToolExecutionContext,
    ToolResult,
    ToolRisk,
    ToolStep,
)
from agent.registry import ToolRegistry


class ApprovalRequiredError(PermissionError):
    pass


class ToolExecutionError(RuntimeError):
    pass


StepHook = Callable[[ToolStep], None]
Redactor = Callable[[str], str]


class AgentRunner:
    def __init__(
        self,
        registry: ToolRegistry,
        *,
        policy: AgentRunPolicy | None = None,
        step_hook: StepHook | None = None,
        redact: Redactor | None = None,
    ) -> None:
        self.registry = registry
        self.policy = policy or AgentRunPolicy()
        self.step_hook = step_hook
        self.redact = redact or (lambda value: value)

    def run(
        self,
        calls: Iterable[ToolCall],
        context: ToolExecutionContext,
        *,
        start_step_index: int = 1,
    ) -> list[ToolResult]:
        calls = list(calls)
        if start_step_index < 1:
            raise ValueError("start_step_index 必须大于 0")
        if start_step_index + len(calls) - 1 > self.policy.max_steps:
            raise ValueError(f"单次 Agent 运行最多执行 {self.policy.max_steps} 个工具步骤")
        started = perf_counter()
        seen_call_ids: set[str] = set()
        results: list[ToolResult] = []
        for index, call in enumerate(calls, start=start_step_index):
            if call.call_id in seen_call_ids:
                raise ValueError(f"重复的工具调用 ID: {call.call_id}")
            seen_call_ids.add(call.call_id)
            if perf_counter() - started >= self.policy.max_duration_seconds:
                raise TimeoutError("Agent 运行超过时间限制")
            result = self._run_one(call, context, index)
            results.append(result)
            if not result.ok:
                if result.exception is not None:
                    raise result.exception
                raise ToolExecutionError(result.error_message or f"工具执行失败: {call.name}")
        return results

    def _run_one(
        self,
        call: ToolCall,
        context: ToolExecutionContext,
        step_index: int,
    ) -> ToolResult:
        started = perf_counter()
        tool = self.registry.get(call.name)
        try:
            if self.policy.allowed_tools is not None and tool.name not in self.policy.allowed_tools:
                raise PermissionError(f"当前 Agent 不允许调用工具: {tool.name}")
            if (
                tool.risk == ToolRisk.WRITE
                and tool.requires_confirmation
                and not context.dry_run
                and not context.approval_granted
                and not context.allow_interactive_approval
            ):
                raise ApprovalRequiredError(f"工具 {tool.name} 需要用户确认")
            payload = tool.input_model.model_validate(call.arguments)
            output = tool.handler(payload, context)
            if not isinstance(output, dict):
                raise TypeError(f"工具 {tool.name} 必须返回字典")
            result = ToolResult(
                call_id=call.call_id,
                tool_name=tool.name,
                ok=True,
                output=output,
                duration_ms=round((perf_counter() - started) * 1000),
            )
            self._emit_step(tool.risk, context, step_index, result, "success")
            return result
        except Exception as exc:
            message = self.redact(str(exc))[:300]
            result = ToolResult(
                call_id=call.call_id,
                tool_name=tool.name,
                ok=False,
                error_type=type(exc).__name__,
                error_message=message,
                duration_ms=round((perf_counter() - started) * 1000),
                exception=exc,
            )
            status = "blocked" if isinstance(exc, (ApprovalRequiredError, PermissionError)) else "error"
            self._emit_step(tool.risk, context, step_index, result, status)
            return result

    def _emit_step(
        self,
        risk: ToolRisk,
        context: ToolExecutionContext,
        step_index: int,
        result: ToolResult,
        status: str,
    ) -> None:
        if self.step_hook is None:
            return
        self.step_hook(
            ToolStep(
                run_id=context.run_id,
                session_id=context.session_id,
                step_index=step_index,
                call_id=result.call_id,
                tool_name=result.tool_name,
                risk=risk,
                status=status,
                duration_ms=result.duration_ms,
                error_type=result.error_type,
                error_message=result.error_message,
            )
        )
