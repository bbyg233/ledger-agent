from __future__ import annotations

from collections.abc import Iterable

from agent.contracts import ToolSpec


class ToolRegistry:
    def __init__(self, tools: Iterable[ToolSpec] = ()) -> None:
        self._tools: dict[str, ToolSpec] = {}
        for tool in tools:
            self.register(tool)

    def register(self, tool: ToolSpec) -> None:
        if not tool.name or not tool.name.replace("_", "").isalnum():
            raise ValueError(f"工具名格式无效: {tool.name}")
        if tool.name in self._tools:
            raise ValueError(f"工具已注册: {tool.name}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> ToolSpec:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise ValueError(f"未注册的工具: {name}") from exc

    def names(self) -> tuple[str, ...]:
        return tuple(self._tools)

    def select(self, names: Iterable[str]) -> ToolRegistry:
        requested = set(names)
        unknown = requested - set(self._tools)
        if unknown:
            raise ValueError(f"未注册的工具: {', '.join(sorted(unknown))}")
        return ToolRegistry(tool for name, tool in self._tools.items() if name in requested)

    def model_schemas(self) -> list[dict]:
        return [tool.model_schema() for tool in self._tools.values()]

    def chat_completion_tools(self) -> list[dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.input_model.model_json_schema(),
                },
            }
            for tool in self._tools.values()
        ]

    def responses_tools(self) -> list[dict]:
        return [
            {
                "type": "function",
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.input_model.model_json_schema(),
            }
            for tool in self._tools.values()
        ]
