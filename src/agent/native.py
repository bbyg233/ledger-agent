from __future__ import annotations

from dataclasses import dataclass, field
import json
from time import perf_counter
from typing import Any, Callable

from agent.checkpoints import CheckpointHook, restore_tool_history, serialize_tool_history
from agent.contracts import ToolCall, ToolExecutionContext, ToolResult
from agent.registry import ToolRegistry
from agent.runner import AgentRunner
from agent.usage import emit_model_usage


class NativeToolCallingError(RuntimeError):
    def __init__(self, message: str, *, executed_steps: int = 0):
        super().__init__(message)
        self.executed_steps = executed_steps


@dataclass
class NativeAgentResult:
    calls: list[ToolCall] = field(default_factory=list)
    results: list[ToolResult] = field(default_factory=list)
    final_text: str = ""
    stopped_for_confirmation: bool = False


SafeOutput = Callable[[str, dict[str, Any]], dict[str, Any]]


def parse_tool_arguments(value: str, tool_name: str) -> dict[str, Any]:
    try:
        arguments = json.loads(value or "{}")
    except json.JSONDecodeError as exc:
        raise NativeToolCallingError(f"工具 {tool_name} 的参数不是有效 JSON") from exc
    if not isinstance(arguments, dict):
        raise NativeToolCallingError(f"工具 {tool_name} 的参数必须是 JSON 对象")
    return arguments


def request_native_tool_call(
    *,
    client: Any,
    model: str,
    registry: ToolRegistry,
    system_prompt: str,
    user_prompt: str,
    responses_api: bool,
) -> ToolCall:
    """Ask the model to select one registered tool without executing it."""
    try:
        if responses_api:
            response = client.responses.create(
                model=model,
                input=[
                    {
                        "role": "system",
                        "content": [{"type": "input_text", "text": system_prompt}],
                    },
                    {
                        "role": "user",
                        "content": [{"type": "input_text", "text": user_prompt}],
                    },
                ],
                tools=registry.responses_tools(),
                tool_choice="required",
            )
            emit_model_usage(
                response,
                api_style="responses",
                purpose="native_tool_selection",
                model=model,
            )
            raw_calls = [item for item in response.output if item.type == "function_call"]
            calls = [
                ToolCall(
                    name=item.name,
                    arguments=parse_tool_arguments(item.arguments, item.name),
                    call_id=item.call_id,
                )
                for item in raw_calls
            ]
        else:
            response = client.chat.completions.create(
                model=model,
                temperature=0,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                tools=registry.chat_completion_tools(),
                tool_choice="required",
            )
            emit_model_usage(
                response,
                api_style="chat",
                purpose="native_tool_selection",
                model=model,
            )
            message = response.choices[0].message
            calls = [
                ToolCall(
                    name=item.function.name,
                    arguments=parse_tool_arguments(item.function.arguments, item.function.name),
                    call_id=item.id,
                )
                for item in list(message.tool_calls or [])
            ]
    except NativeToolCallingError:
        raise
    except Exception as exc:
        raise NativeToolCallingError(f"原生工具选择失败: {exc}") from exc

    if len(calls) != 1:
        raise NativeToolCallingError("模型必须且只能选择一个账本工具")
    call = calls[0]
    try:
        tool = registry.get(call.name)
        payload = tool.input_model.model_validate(call.arguments)
    except Exception as exc:
        raise NativeToolCallingError(f"工具 {call.name} 参数校验失败: {exc}") from exc
    return ToolCall(
        name=call.name,
        arguments=payload.model_dump(exclude_none=True),
        call_id=call.call_id,
    )


class NativeToolLoop:
    def __init__(
        self,
        *,
        client: Any,
        model: str,
        registry: ToolRegistry,
        runner: AgentRunner,
        context: ToolExecutionContext,
        safe_output: SafeOutput,
        max_model_calls: int = 6,
    ) -> None:
        self.client = client
        self.model = model
        self.registry = registry
        self.runner = runner
        self.context = context
        self.safe_output = safe_output
        self.max_model_calls = max_model_calls

    def run(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        responses_api: bool,
        image_data_urls: tuple[str, ...] = (),
        checkpoint_state: dict[str, Any] | None = None,
        checkpoint_hook: CheckpointHook | None = None,
    ) -> NativeAgentResult:
        if responses_api:
            return self._run_responses(
                system_prompt,
                user_prompt,
                image_data_urls=image_data_urls,
                checkpoint_state=checkpoint_state,
                checkpoint_hook=checkpoint_hook,
            )
        return self._run_chat(
            system_prompt,
            user_prompt,
            image_data_urls=image_data_urls,
            checkpoint_state=checkpoint_state,
            checkpoint_hook=checkpoint_hook,
        )

    def _run_chat(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        image_data_urls: tuple[str, ...],
        checkpoint_state: dict[str, Any] | None,
        checkpoint_hook: CheckpointHook | None,
    ) -> NativeAgentResult:
        state = checkpoint_state if checkpoint_state and checkpoint_state.get("api_style") == "chat" else {}
        messages = state.get("messages")
        if not isinstance(messages, list):
            messages = [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": (
                        user_prompt
                        if not image_data_urls
                        else [
                            {"type": "text", "text": user_prompt},
                            *[
                                {"type": "image_url", "image_url": {"url": url}}
                                for url in image_data_urls
                            ],
                        ]
                    ),
                },
            ]
        calls, results = restore_tool_history(state.get("history"))
        outcome = NativeAgentResult(calls=calls, results=results)
        model_calls = max(0, int(state.get("model_calls") or 0))
        invalid_argument_retries = 0
        missing_tool_retries = 0
        for _ in range(model_calls, self.max_model_calls):
            self._checkpoint(
                checkpoint_hook,
                api_style="chat",
                model_calls=model_calls,
                messages=messages,
                outcome=outcome,
            )
            try:
                started = perf_counter()
                response = self.client.chat.completions.create(
                    model=self.model,
                    temperature=0,
                    messages=messages,
                    tools=self.registry.chat_completion_tools(),
                    tool_choice="required" if not outcome.results else "auto",
                )
            except Exception as exc:
                raise NativeToolCallingError(
                    f"原生 Chat 工具调用失败: {exc}", executed_steps=len(outcome.results)
                ) from exc
            emit_model_usage(
                response,
                api_style="chat",
                purpose="native_agent",
                model=self.model,
                duration_ms=round((perf_counter() - started) * 1000),
            )
            model_calls += 1
            message = response.choices[0].message
            tool_calls = list(message.tool_calls or [])
            if not tool_calls:
                final_text = (message.content or "").strip()
                if not outcome.results:
                    if missing_tool_retries < 1:
                        missing_tool_retries += 1
                        continue
                    raise NativeToolCallingError("模型没有调用任何账本工具")
                outcome.final_text = final_text
                return outcome
            messages.append(message.model_dump(exclude_none=True))
            try:
                calls = [
                    ToolCall(
                        name=item.function.name,
                        arguments=parse_tool_arguments(item.function.arguments, item.function.name),
                        call_id=item.id,
                    )
                    for item in tool_calls
                ]
            except NativeToolCallingError:
                if invalid_argument_retries >= 1:
                    raise
                # Retry the same untouched turn once; no tool has run yet.
                invalid_argument_retries += 1
                continue
            results = self.runner.run(
                calls,
                self.context,
                start_step_index=len(outcome.results) + 1,
            )
            outcome.calls.extend(calls)
            outcome.results.extend(results)
            for call, result in zip(calls, results, strict=True):
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.call_id,
                        "content": json.dumps(
                            self.safe_output(call.name, result.output), ensure_ascii=False
                        ),
                    }
                )
            self._checkpoint(
                checkpoint_hook,
                api_style="chat",
                model_calls=model_calls,
                messages=messages,
                outcome=outcome,
            )
            if any(self._requires_user_stop(call.name) for call in calls):
                outcome.stopped_for_confirmation = True
                return outcome
        raise NativeToolCallingError(
            "原生工具循环超过模型调用上限", executed_steps=len(outcome.results)
        )

    def _run_responses(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        image_data_urls: tuple[str, ...],
        checkpoint_state: dict[str, Any] | None,
        checkpoint_hook: CheckpointHook | None,
    ) -> NativeAgentResult:
        state = (
            checkpoint_state
            if checkpoint_state and checkpoint_state.get("api_style") == "responses"
            else {}
        )
        request_input = state.get("request_input")
        if not isinstance(request_input, list):
            request_input = [
                {"role": "system", "content": [{"type": "input_text", "text": system_prompt}]},
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": user_prompt},
                        *[
                            {"type": "input_image", "image_url": url}
                            for url in image_data_urls
                        ],
                    ],
                },
            ]
        previous_response_id = str(state.get("previous_response_id") or "") or None
        calls, results = restore_tool_history(state.get("history"))
        outcome = NativeAgentResult(calls=calls, results=results)
        model_calls = max(0, int(state.get("model_calls") or 0))
        invalid_argument_retries = 0
        missing_tool_retries = 0
        for _ in range(model_calls, self.max_model_calls):
            self._checkpoint(
                checkpoint_hook,
                api_style="responses",
                model_calls=model_calls,
                request_input=request_input,
                previous_response_id=previous_response_id,
                outcome=outcome,
            )
            request: dict[str, Any] = {
                "model": self.model,
                "input": request_input,
                "tools": self.registry.responses_tools(),
                "store": True,
                "tool_choice": "required" if not outcome.results else "auto",
            }
            if previous_response_id:
                request["previous_response_id"] = previous_response_id
            try:
                started = perf_counter()
                response = self.client.responses.create(**request)
            except Exception as exc:
                raise NativeToolCallingError(
                    f"原生 Responses 工具调用失败: {exc}", executed_steps=len(outcome.results)
                ) from exc
            emit_model_usage(
                response,
                api_style="responses",
                purpose="native_agent",
                model=self.model,
                duration_ms=round((perf_counter() - started) * 1000),
            )
            model_calls += 1
            function_calls = [item for item in response.output if item.type == "function_call"]
            if not function_calls:
                final_text = (response.output_text or "").strip()
                if not outcome.results:
                    if missing_tool_retries < 1:
                        missing_tool_retries += 1
                        continue
                    raise NativeToolCallingError("模型没有调用任何账本工具")
                outcome.final_text = final_text
                return outcome
            try:
                calls = [
                    ToolCall(
                        name=item.name,
                        arguments=parse_tool_arguments(item.arguments, item.name),
                        call_id=item.call_id,
                    )
                    for item in function_calls
                ]
            except NativeToolCallingError:
                if invalid_argument_retries >= 1:
                    raise
                # Retry the same untouched turn once; no tool has run yet.
                invalid_argument_retries += 1
                continue
            results = self.runner.run(
                calls,
                self.context,
                start_step_index=len(outcome.results) + 1,
            )
            outcome.calls.extend(calls)
            outcome.results.extend(results)
            if any(self._requires_user_stop(call.name) for call in calls):
                outcome.stopped_for_confirmation = True
                return outcome
            previous_response_id = response.id
            request_input = [
                {
                    "type": "function_call_output",
                    "call_id": call.call_id,
                    "output": json.dumps(
                        self.safe_output(call.name, result.output), ensure_ascii=False
                    ),
                }
                for call, result in zip(calls, results, strict=True)
            ]
            self._checkpoint(
                checkpoint_hook,
                api_style="responses",
                model_calls=model_calls,
                request_input=request_input,
                previous_response_id=previous_response_id,
                outcome=outcome,
            )
        raise NativeToolCallingError(
            "原生工具循环超过模型调用上限", executed_steps=len(outcome.results)
        )

    @staticmethod
    def _requires_user_stop(tool_name: str) -> bool:
        return tool_name in {
            "ask_clarification",
            "record_transactions",
            "propose_subscriptions",
            "propose_subscription_charge",
            "propose_liability_statement",
            "propose_liability_payment",
            "propose_liability_charge",
        }

    @staticmethod
    def _checkpoint(
        hook: CheckpointHook | None,
        *,
        api_style: str,
        model_calls: int,
        outcome: NativeAgentResult,
        messages: list[dict[str, Any]] | None = None,
        request_input: list[dict[str, Any]] | None = None,
        previous_response_id: str | None = None,
    ) -> None:
        if hook is None:
            return
        state: dict[str, Any] = {
            "version": 1,
            "api_style": api_style,
            "model_calls": model_calls,
            "executed_steps": len(outcome.results),
            "history": serialize_tool_history(outcome.calls, outcome.results),
        }
        if messages is not None:
            state["messages"] = messages
        if request_input is not None:
            state["request_input"] = request_input
            state["previous_response_id"] = previous_response_id or ""
        hook(state)
