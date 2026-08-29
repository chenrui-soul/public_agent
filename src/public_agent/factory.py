from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from public_agent.core.events import EventSink
from public_agent.core.model import ModelProvider
from public_agent.core.runtime import AgentRuntime
from public_agent.core.types import AgentSpec, RunContext, RunResult
from public_agent.core.verifier import OutputVerifier
from public_agent.domains.loader import DomainPackageLoader
from public_agent.knowledge.base import KnowledgeRetriever
from public_agent.memory.base import MemoryStore
from public_agent.policies.base import PolicyEngine
from public_agent.tools.registry import ToolRegistry


@dataclass(frozen=True, slots=True)
class Agent:
    spec: AgentSpec
    runtime: AgentRuntime

    async def run(self, task: str, *, context: RunContext) -> RunResult:
        return await self.runtime.run(agent=self.spec, task=task, context=context)


class AgentFactory:
    def __init__(self, loader: DomainPackageLoader | None = None) -> None:
        self._loader = loader or DomainPackageLoader()

    def create(
        self,
        *,
        domain_path: str | Path,
        model: ModelProvider,
        tools: ToolRegistry,
        knowledge: KnowledgeRetriever | None = None,
        knowledge_timeout_seconds: float = 5.0,
        memory: MemoryStore | None = None,
        policies: PolicyEngine | None = None,
        events: EventSink | None = None,
        verifier: OutputVerifier | None = None,
    ) -> Agent:
        package = self._loader.load(domain_path)
        return self.create_from_spec(
            spec=package.to_agent_spec(),
            model=model,
            tools=tools,
            knowledge=knowledge,
            knowledge_timeout_seconds=knowledge_timeout_seconds,
            memory=memory,
            policies=policies,
            events=events,
            verifier=verifier,
        )

    def create_from_spec(
        self,
        *,
        spec: AgentSpec,
        model: ModelProvider,
        tools: ToolRegistry,
        knowledge: KnowledgeRetriever | None = None,
        knowledge_timeout_seconds: float = 5.0,
        memory: MemoryStore | None = None,
        policies: PolicyEngine | None = None,
        events: EventSink | None = None,
        verifier: OutputVerifier | None = None,
    ) -> Agent:
        missing_tools = [name for name in spec.allowed_tools if not self._has_tool(tools, name)]
        if missing_tools:
            missing = ", ".join(sorted(missing_tools))
            raise ValueError(f"Domain package requires unregistered tools: {missing}")

        runtime = AgentRuntime(
            model=model,
            tools=tools,
            knowledge=knowledge,
            knowledge_timeout_seconds=knowledge_timeout_seconds,
            memory=memory,
            policies=policies,
            events=events,
            verifier=verifier,
        )
        return Agent(spec=spec, runtime=runtime)

    @staticmethod
    def _has_tool(registry: ToolRegistry, name: str) -> bool:
        try:
            registry.get(name)
        except KeyError:
            return False
        return True


class ActiveAgentSpecLoader(Protocol):
    async def load_active_spec(self, *, tenant_id: str, agent_id: str) -> AgentSpec: ...


class ActiveAgentAssembler:
    def __init__(
        self,
        *,
        specs: ActiveAgentSpecLoader,
        model: ModelProvider,
        tools: ToolRegistry,
        factory: AgentFactory | None = None,
        knowledge: KnowledgeRetriever | None = None,
        knowledge_timeout_seconds: float = 5.0,
        memory: MemoryStore | None = None,
        policies: PolicyEngine | None = None,
        events: EventSink | None = None,
        verifier: OutputVerifier | None = None,
    ) -> None:
        self._specs = specs
        self._model = model
        self._tools = tools
        self._factory = factory or AgentFactory()
        self._knowledge = knowledge
        self._knowledge_timeout_seconds = knowledge_timeout_seconds
        self._memory = memory
        self._policies = policies
        self._events = events
        self._verifier = verifier

    async def load(self, *, tenant_id: str, agent_id: str) -> Agent:
        spec = await self._specs.load_active_spec(
            tenant_id=tenant_id,
            agent_id=agent_id,
        )
        if spec.id != agent_id:
            raise RuntimeError("Active agent specification does not match the requested agent")
        return self._factory.create_from_spec(
            spec=spec,
            model=self._model,
            tools=self._tools,
            knowledge=self._knowledge,
            knowledge_timeout_seconds=self._knowledge_timeout_seconds,
            memory=self._memory,
            policies=self._policies,
            events=self._events,
            verifier=self._verifier,
        )
