from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from public_agent.core.types import ToolDefinition


class ToolContext(BaseModel):
    model_config = ConfigDict(frozen=True)

    tenant_id: str
    agent_id: str
    run_id: UUID
    user_id: str | None = None
    idempotency_key: str | None = None


class ToolExecutionResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    success: bool
    output: Any = None
    error: str | None = None
    duration_ms: float = 0


class Tool(Protocol):
    @property
    def definition(self) -> ToolDefinition:
        """Describe the callable contract and risk level."""

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> Any:
        """Execute one validated tool call."""


class FunctionTool:
    def __init__(
        self,
        definition: ToolDefinition,
        function: Callable[[dict[str, Any], ToolContext], Awaitable[Any]],
    ) -> None:
        self._definition = definition
        self._function = function

    @property
    def definition(self) -> ToolDefinition:
        return self._definition

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> Any:
        return await self._function(arguments, context)
