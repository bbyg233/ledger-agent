from copy import deepcopy
from types import SimpleNamespace

from pydantic import BaseModel

from agent import (
    AgentRunner,
    ToolExecutionContext,
    ToolRegistry,
    ToolSpec,
    request_native_tool_call,
)
from agent.native import NativeToolLoop
from agent.native import NativeToolCallingError


class ValueInput(BaseModel):
    value: int


class FakeMessage:
    def __init__(self, *, tool_calls=None, content=""):
        self.tool_calls = tool_calls or []
        self.content = content

    def model_dump(self, exclude_none=True):
        return {"role": "assistant", "content": self.content, "tool_calls": self.tool_calls}


def chat_tool_call(call_id, name, arguments):
    return SimpleNamespace(
        id=call_id,
        function=SimpleNamespace(name=name, arguments=arguments),
    )


def test_native_chat_loop_can_use_tool_results_for_a_second_step():
    responses = [
        SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=FakeMessage(
                        tool_calls=[chat_tool_call("call-1", "increment", '{"value":2}')]
                    )
                )
            ]
        ),
        SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=FakeMessage(
                        tool_calls=[chat_tool_call("call-2", "double", '{"value":3}')]
                    )
                )
            ]
        ),
        SimpleNamespace(choices=[SimpleNamespace(message=FakeMessage(content="最终结果是 6。"))]),
    ]
    requests = []

    def create(**kwargs):
        requests.append(deepcopy(kwargs))
        return responses.pop(0)

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    registry = ToolRegistry(
        [
            ToolSpec("increment", "加一", ValueInput, lambda payload, context: {"value": payload.value + 1}),
            ToolSpec("double", "翻倍", ValueInput, lambda payload, context: {"value": payload.value * 2}),
        ]
    )
    steps = []
    loop = NativeToolLoop(
        client=client,
        model="demo",
        registry=registry,
        runner=AgentRunner(registry, step_hook=steps.append),
        context=ToolExecutionContext(state=None, run_id="native-chat"),
        safe_output=lambda name, output: output,
    )

    result = loop.run("system", "user", responses_api=False)

    assert result.final_text == "最终结果是 6。"
    assert [call.name for call in result.calls] == ["increment", "double"]
    assert [step.step_index for step in steps] == [1, 2]
    assert requests[1]["messages"][-1]["role"] == "tool"
    assert '"value": 3' in requests[1]["messages"][-1]["content"]


def test_native_responses_loop_returns_function_output_to_previous_response():
    responses = [
        SimpleNamespace(
            id="resp-1",
            output=[
                SimpleNamespace(
                    type="function_call",
                    call_id="call-r1",
                    name="double",
                    arguments='{"value":4}',
                )
            ],
            output_text="",
        ),
        SimpleNamespace(id="resp-2", output=[], output_text="结果是 8。"),
    ]
    requests = []

    def create(**kwargs):
        requests.append(kwargs)
        return responses.pop(0)

    client = SimpleNamespace(responses=SimpleNamespace(create=create))
    registry = ToolRegistry(
        [ToolSpec("double", "翻倍", ValueInput, lambda payload, context: {"value": payload.value * 2})]
    )
    loop = NativeToolLoop(
        client=client,
        model="demo",
        registry=registry,
        runner=AgentRunner(registry),
        context=ToolExecutionContext(state=None, run_id="native-responses"),
        safe_output=lambda name, output: output,
    )

    result = loop.run("system", "user", responses_api=True)

    assert result.final_text == "结果是 8。"
    assert requests[1]["previous_response_id"] == "resp-1"
    assert requests[1]["input"][0]["type"] == "function_call_output"
    assert requests[1]["input"][0]["call_id"] == "call-r1"
    assert '"value": 8' in requests[1]["input"][0]["output"]


def test_native_responses_loop_retries_one_malformed_tool_call_before_execution():
    responses = [
        SimpleNamespace(
            id="resp-invalid",
            output=[
                SimpleNamespace(
                    type="function_call",
                    call_id="call-invalid",
                    name="double",
                    arguments='{"value":',
                )
            ],
            output_text="",
        ),
        SimpleNamespace(
            id="resp-valid",
            output=[
                SimpleNamespace(
                    type="function_call",
                    call_id="call-valid",
                    name="double",
                    arguments='{"value":4}',
                )
            ],
            output_text="",
        ),
        SimpleNamespace(id="resp-final", output=[], output_text="结果是 8。"),
    ]
    requests = []

    def create(**kwargs):
        requests.append(deepcopy(kwargs))
        return responses.pop(0)

    client = SimpleNamespace(responses=SimpleNamespace(create=create))
    registry = ToolRegistry(
        [ToolSpec("double", "翻倍", ValueInput, lambda payload, context: {"value": payload.value * 2})]
    )
    loop = NativeToolLoop(
        client=client,
        model="demo",
        registry=registry,
        runner=AgentRunner(registry),
        context=ToolExecutionContext(state=None, run_id="native-retry"),
        safe_output=lambda name, output: output,
    )

    result = loop.run("system", "user", responses_api=True)

    assert result.final_text == "结果是 8。"
    assert [call.call_id for call in result.calls] == ["call-valid"]
    assert "previous_response_id" not in requests[1]
    assert requests[2]["previous_response_id"] == "resp-valid"


def test_registry_exports_both_openai_tool_schema_shapes():
    registry = ToolRegistry(
        [ToolSpec("double", "翻倍", ValueInput, lambda payload, context: {"value": payload.value * 2})]
    )

    chat = registry.chat_completion_tools()[0]
    responses = registry.responses_tools()[0]

    assert chat["type"] == "function"
    assert chat["function"]["name"] == "double"
    assert responses["type"] == "function"
    assert responses["name"] == "double"


def test_native_tool_selection_uses_registry_schema_as_source_of_truth():
    request = {}

    def create(**kwargs):
        request.update(deepcopy(kwargs))
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=FakeMessage(
                        tool_calls=[chat_tool_call("selected-1", "double", '{"value":4}')]
                    )
                )
            ]
        )

    registry = ToolRegistry(
        [ToolSpec("double", "翻倍", ValueInput, lambda payload, context: {})]
    )

    call = request_native_tool_call(
        client=SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        ),
        model="demo",
        registry=registry,
        system_prompt="system",
        user_prompt="user",
        responses_api=False,
    )

    assert call.name == "double"
    assert call.arguments == {"value": 4}
    assert request["tool_choice"] == "required"
    assert request["tools"] == registry.chat_completion_tools()


def test_registry_select_preserves_only_requested_pydantic_tools():
    registry = ToolRegistry(
        [
            ToolSpec("increment", "加一", ValueInput, lambda payload, context: {}),
            ToolSpec("double", "翻倍", ValueInput, lambda payload, context: {}),
        ]
    )

    selected = registry.select({"double"})

    assert selected.names() == ("double",)
    assert selected.model_schemas()[0]["input_schema"] == ValueInput.model_json_schema()


def test_native_chat_loop_resumes_after_completed_tool_step():
    first_responses = [
        SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=FakeMessage(
                        tool_calls=[chat_tool_call("call-resume", "double", '{"value":5}')]
                    )
                )
            ]
        )
    ]
    calls = {"tools": 0}
    checkpoint = {}

    def first_create(**kwargs):
        if first_responses:
            return first_responses.pop(0)
        raise RuntimeError("temporary model outage")

    def handle(payload, context):
        calls["tools"] += 1
        return {"value": payload.value * 2}

    registry = ToolRegistry([ToolSpec("double", "翻倍", ValueInput, handle)])
    first_loop = NativeToolLoop(
        client=SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=first_create))
        ),
        model="demo",
        registry=registry,
        runner=AgentRunner(registry),
        context=ToolExecutionContext(state=None, run_id="resume-run"),
        safe_output=lambda name, output: output,
    )

    try:
        first_loop.run(
            "system",
            "user",
            responses_api=False,
            checkpoint_hook=lambda state: checkpoint.update(deepcopy(state)),
        )
    except NativeToolCallingError:
        pass
    else:
        raise AssertionError("第一次运行应在第二次模型调用时失败")

    resumed_requests = []

    def resumed_create(**kwargs):
        resumed_requests.append(deepcopy(kwargs))
        return SimpleNamespace(
            choices=[SimpleNamespace(message=FakeMessage(content="恢复完成，结果是 10。"))]
        )

    resumed_loop = NativeToolLoop(
        client=SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=resumed_create))
        ),
        model="demo",
        registry=registry,
        runner=AgentRunner(registry),
        context=ToolExecutionContext(state=None, run_id="resume-run"),
        safe_output=lambda name, output: output,
    )
    result = resumed_loop.run(
        "ignored",
        "ignored",
        responses_api=False,
        checkpoint_state=checkpoint,
    )

    assert result.final_text == "恢复完成，结果是 10。"
    assert calls["tools"] == 1
    assert resumed_requests[0]["messages"][-1]["role"] == "tool"
