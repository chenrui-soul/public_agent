from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, ConfigDict

from public_agent.core.types import AgentSpec, RunContext, ToolCall, ToolDefinition, ToolRisk


class PolicyDecision(BaseModel):
    model_config = ConfigDict(frozen=True)

    allowed: bool
    requires_approval: bool = False
    reason: str


class PolicyEngine(Protocol):
    async def authorize_tool(
        self,
        *,
        agent: AgentSpec,
        run_context: RunContext,
        tool: ToolDefinition,
        call: ToolCall,
    ) -> PolicyDecision:
        """Decide whether a tool call is allowed or needs human approval."""


class DefaultPolicyEngine:
    async def authorize_tool(
        self,
        *,
        agent: AgentSpec,
        run_context: RunContext,
        tool: ToolDefinition,
        call: ToolCall,
    ) -> PolicyDecision:
        del run_context, call
        if tool.name not in agent.allowed_tools:
            return PolicyDecision(allowed=False, reason="Tool is not allowed by the agent profile")
        if tool.risk is ToolRisk.IRREVERSIBLE:
            return PolicyDecision(allowed=False, reason="Irreversible tools are denied by default")
        if tool.requires_approval or tool.risk is ToolRisk.HIGH_RISK_WRITE:
            return PolicyDecision(
                allowed=True,
                requires_approval=True,
                reason="High-risk tool call requires human approval",
            )
        return PolicyDecision(allowed=True, reason="Tool call allowed by default policy")
