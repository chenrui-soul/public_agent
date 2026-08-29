from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from public_agent.core.types import utc_now


class MemoryType(StrEnum):
    WORKING = "working"
    EPISODIC = "episodic"
    SEMANTIC = "semantic"
    PROCEDURAL = "procedural"
    FAILURE = "failure"


class MemoryRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID = Field(default_factory=uuid4)
    tenant_id: str
    agent_id: str
    namespace: str
    memory_type: MemoryType
    content: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    confidence: float = Field(default=1.0, ge=0, le=1)
    importance: float = Field(default=0.5, ge=0, le=1)
    created_at: datetime = Field(default_factory=utc_now)
    expires_at: datetime | None = None


class MemoryQuery(BaseModel):
    model_config = ConfigDict(frozen=True)

    tenant_id: str
    agent_id: str
    namespace: str
    text: str
    limit: int = Field(default=5, ge=1, le=100)
    memory_types: tuple[MemoryType, ...] = ()


class MemoryStore(Protocol):
    async def save(self, memory: MemoryRecord) -> None:
        """Persist one memory record."""

    async def search(self, query: MemoryQuery) -> tuple[MemoryRecord, ...]:
        """Return memories scoped to tenant, agent, and namespace."""

    async def deactivate(self, memory_id: UUID) -> None:
        """Stop one published memory from participating in retrieval."""

    async def activate(self, memory_id: UUID) -> None:
        """Restore one previously deactivated memory to retrieval."""
