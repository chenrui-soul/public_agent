from __future__ import annotations

from collections.abc import Iterable

from public_agent.core.types import ModelRequest, ModelResponse


class ScriptedModelProvider:
    """Deterministic provider used by examples and offline tests."""

    def __init__(self, responses: Iterable[ModelResponse]) -> None:
        self._responses = list(responses)
        self.requests: list[ModelRequest] = []

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        if not self._responses:
            raise RuntimeError("ScriptedModelProvider has no remaining responses")
        return self._responses.pop(0)
