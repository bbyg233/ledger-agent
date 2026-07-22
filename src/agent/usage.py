from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass
from typing import Any, Callable, Iterator, Mapping


@dataclass(frozen=True)
class ModelUsage:
    api_style: str
    purpose: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cached_tokens: int = 0
    reasoning_tokens: int = 0
    reported_cost: float | None = None
    response_id: str = ""
    duration_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


UsageObserver = Callable[[ModelUsage], None]
_USAGE_OBSERVER: ContextVar[UsageObserver | None] = ContextVar(
    "ledger_agent_usage_observer", default=None
)


def _value(source: Any, name: str, default: Any = 0) -> Any:
    if source is None:
        return default
    if isinstance(source, Mapping):
        return source.get(name, default)
    return getattr(source, name, default)


def _integer(source: Any, name: str) -> int:
    try:
        return max(0, int(_value(source, name, 0) or 0))
    except (TypeError, ValueError):
        return 0


def _optional_float(source: Any, name: str) -> float | None:
    try:
        value = _value(source, name, None)
        return None if value is None else max(0.0, float(value))
    except (TypeError, ValueError):
        return None


def extract_model_usage(
    response: Any,
    *,
    api_style: str,
    purpose: str,
    model: str,
    duration_ms: int = 0,
) -> ModelUsage:
    usage = _value(response, "usage", None)
    if api_style == "responses":
        input_tokens = _integer(usage, "input_tokens")
        output_tokens = _integer(usage, "output_tokens")
        cached_tokens = _integer(_value(usage, "input_tokens_details", None), "cached_tokens")
        reasoning_tokens = _integer(
            _value(usage, "output_tokens_details", None), "reasoning_tokens"
        )
    else:
        input_tokens = _integer(usage, "prompt_tokens")
        output_tokens = _integer(usage, "completion_tokens")
        cached_tokens = _integer(_value(usage, "prompt_tokens_details", None), "cached_tokens")
        reasoning_tokens = _integer(
            _value(usage, "completion_tokens_details", None), "reasoning_tokens"
        )
    total_tokens = _integer(usage, "total_tokens") or input_tokens + output_tokens
    return ModelUsage(
        api_style=api_style,
        purpose=purpose,
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        cached_tokens=cached_tokens,
        reasoning_tokens=reasoning_tokens,
        reported_cost=_optional_float(usage, "cost"),
        response_id=str(_value(response, "id", "") or ""),
        duration_ms=max(0, int(duration_ms)),
    )


def emit_model_usage(
    response: Any,
    *,
    api_style: str,
    purpose: str,
    model: str,
    duration_ms: int = 0,
) -> ModelUsage:
    usage = extract_model_usage(
        response,
        api_style=api_style,
        purpose=purpose,
        model=model,
        duration_ms=duration_ms,
    )
    observer = _USAGE_OBSERVER.get()
    if observer is not None:
        observer(usage)
    return usage


@contextmanager
def observe_model_usage(observer: UsageObserver) -> Iterator[None]:
    token = _USAGE_OBSERVER.set(observer)
    try:
        yield
    finally:
        _USAGE_OBSERVER.reset(token)
