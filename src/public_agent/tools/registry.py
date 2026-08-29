from __future__ import annotations

import asyncio
from time import perf_counter
from typing import Any

from jsonschema import Draft202012Validator

from public_agent.core.types import ToolDefinition
from public_agent.tools.base import Tool, ToolContext, ToolExecutionResult


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        name = tool.definition.name
        if name in self._tools:
            raise ValueError(f"Tool already registered: {name}")
        Draft202012Validator.check_schema(tool.definition.input_schema)
        if tool.definition.output_schema is not None:
            Draft202012Validator.check_schema(tool.definition.output_schema)
        self._tools[name] = tool

    def get(self, name: str) -> Tool:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise KeyError(f"Unknown tool: {name}") from exc

    def definitions(self, allowed_names: tuple[str, ...]) -> tuple[ToolDefinition, ...]:
        return tuple(self.get(name).definition for name in allowed_names)

    async def execute(
        self,
        name: str,
        arguments: dict[str, Any],
        context: ToolContext,
    ) -> ToolExecutionResult:
        tool = self.get(name)
        validator = Draft202012Validator(tool.definition.input_schema)
        errors = sorted(validator.iter_errors(arguments), key=lambda error: list(error.path))
        if errors:
            message = "; ".join(error.message for error in errors)
            return ToolExecutionResult(success=False, error=f"Invalid tool arguments: {message}")

        started = perf_counter()
        try:
            output = await asyncio.wait_for(
                tool.execute(arguments, context),
                timeout=tool.definition.timeout_seconds,
            )
            if tool.definition.output_schema is not None:
                Draft202012Validator(tool.definition.output_schema).validate(output)
            return ToolExecutionResult(
                success=True,
                output=output,
                duration_ms=(perf_counter() - started) * 1000,
            )
        except TimeoutError:
            return ToolExecutionResult(
                success=False,
                error=f"Tool timed out after {tool.definition.timeout_seconds} seconds",
                duration_ms=(perf_counter() - started) * 1000,
            )
        except Exception as exc:
            return ToolExecutionResult(
                success=False,
                error=f"{type(exc).__name__}: {exc}",
                duration_ms=(perf_counter() - started) * 1000,
            )
