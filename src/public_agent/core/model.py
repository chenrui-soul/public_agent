from __future__ import annotations

from typing import Protocol

from public_agent.core.types import ModelRequest, ModelResponse


class ModelProvider(Protocol):
    """Provider-neutral model contract used by the runtime."""

    async def complete(self, request: ModelRequest) -> ModelResponse:
        """Return the next assistant response or structured tool calls."""
