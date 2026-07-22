from pydantic import BaseModel, Field, ValidationError
import pytest

from agent import (
    AgentRunPolicy,
    AgentRunner,
    ApprovalRequiredError,
    ToolCall,
    ToolExecutionContext,
    ToolRegistry,
    ToolRisk,
    ToolSpec,
)


class AmountInput(BaseModel):
    amount: float = Field(gt=0)


def test_runner_validates_and_executes_registered_tool():
    registry = ToolRegistry(
        [
            ToolSpec(
                name="double_amount",
                description="Double an amount.",
                input_model=AmountInput,
                handler=lambda payload, context: {"value": payload.amount * 2},
            )
        ]
    )
    runner = AgentRunner(registry)

    result = runner.run(
        [ToolCall(name="double_amount", arguments={"amount": 12})],
        ToolExecutionContext(state=None, run_id="run-1"),
    )[0]

    assert result.ok is True
    assert result.output == {"value": 24}
    assert registry.model_schemas()[0]["input_schema"]["properties"]["amount"]


def test_runner_blocks_unapproved_write_and_emits_step():
    called = False
    steps = []

    def write_handler(payload, context):
        nonlocal called
        called = True
        return {"written": True}

    registry = ToolRegistry(
        [
            ToolSpec(
                name="write_ledger",
                description="Write ledger data.",
                input_model=AmountInput,
                handler=write_handler,
                risk=ToolRisk.WRITE,
                requires_confirmation=True,
            )
        ]
    )
    runner = AgentRunner(registry, step_hook=steps.append)

    with pytest.raises(ApprovalRequiredError):
        runner.run(
            [ToolCall(name="write_ledger", arguments={"amount": 10})],
            ToolExecutionContext(state=None, run_id="run-2"),
        )

    assert called is False
    assert steps[0].status == "blocked"
    assert steps[0].risk == ToolRisk.WRITE


def test_runner_rejects_invalid_arguments_and_too_many_steps():
    registry = ToolRegistry(
        [
            ToolSpec(
                name="read_amount",
                description="Read an amount.",
                input_model=AmountInput,
                handler=lambda payload, context: {"amount": payload.amount},
            )
        ]
    )
    runner = AgentRunner(registry, policy=AgentRunPolicy(max_steps=1))

    with pytest.raises(ValidationError):
        runner.run(
            [ToolCall(name="read_amount", arguments={"amount": 0})],
            ToolExecutionContext(state=None, run_id="run-3"),
        )
    with pytest.raises(ValueError, match="最多执行 1 个工具步骤"):
        runner.run(
            [
                ToolCall(name="read_amount", arguments={"amount": 1}),
                ToolCall(name="read_amount", arguments={"amount": 2}),
            ],
            ToolExecutionContext(state=None, run_id="run-4"),
        )
