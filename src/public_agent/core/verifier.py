from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, ConfigDict

from public_agent.core.types import AgentSpec, RunContext


class VerificationResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    passed: bool
    reason: str


class OutputVerifier(Protocol):
    async def verify(
        self,
        *,
        task: str,
        output: str,
        agent: AgentSpec,
        run_context: RunContext,
    ) -> VerificationResult:
        """Determine whether an output meets the configured success contract."""


class NonEmptyOutputVerifier:
    async def verify(
        self,
        *,
        task: str,
        output: str,
        agent: AgentSpec,
        run_context: RunContext,
    ) -> VerificationResult:
        del task, agent, run_context
        if output.strip():
            return VerificationResult(passed=True, reason="Output is non-empty")
        return VerificationResult(passed=False, reason="Output is empty")
