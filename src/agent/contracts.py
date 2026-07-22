from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Callable
from uuid import uuid4

from pydantic import BaseModel


class ToolRisk(StrEnum):
    READ_ONLY = "read_only"
    WRITE = "write"


@dataclass(frozen=True)
class ToolExecutionContext:
    state: Any
    run_id: str
    session_id: str = ""
    dry_run: bool = False
    approval_granted: bool = False
    allow_interactive_approval: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


ToolHandler = Callable[[BaseModel, ToolExecutionContext], dict[str, Any]]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_model: type[BaseModel]
    handler: ToolHandler
    risk: ToolRisk = ToolRisk.READ_ONLY
    requires_confirmation: bool = False

    def model_schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_model.model_json_schema(),
            "risk": self.risk.value,
            "requires_confirmation": self.requires_confirmation,
        }


@dataclass(frozen=True)
class ToolCall:
    name: str
    arguments: dict[str, Any]
    call_id: str = field(default_factory=lambda: uuid4().hex)


@dataclass
class ToolResult:
    call_id: str
    tool_name: str
    ok: bool
    output: dict[str, Any] = field(default_factory=dict)
    error_type: str = ""
    error_message: str = ""
    duration_ms: int = 0
    exception: Exception | None = field(default=None, repr=False)


@dataclass(frozen=True)
class ToolStep:
    run_id: str
    session_id: str
    step_index: int
    call_id: str
    tool_name: str
    risk: ToolRisk
    status: str
    duration_ms: int
    error_type: str = ""
    error_message: str = ""


@dataclass(frozen=True)
class AgentRunPolicy:
    max_steps: int = 5
    max_duration_seconds: float = 120.0
    allowed_tools: frozenset[str] | None = None

    def __post_init__(self) -> None:
        if self.max_steps < 1:
            raise ValueError("max_steps 必须大于 0")
        if self.max_duration_seconds <= 0:
            raise ValueError("max_duration_seconds 必须大于 0")
