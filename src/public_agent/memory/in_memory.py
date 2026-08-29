from __future__ import annotations

import re
from datetime import UTC, datetime
from uuid import UUID

from public_agent.memory.base import MemoryQuery, MemoryRecord


class InMemoryMemoryStore:
    def __init__(self) -> None:
        self._records: dict[str, MemoryRecord] = {}
        self._inactive: set[str] = set()

    async def save(self, memory: MemoryRecord) -> None:
        self._records[str(memory.id)] = memory
        self._inactive.discard(str(memory.id))

    async def deactivate(self, memory_id: UUID) -> None:
        key = str(memory_id)
        if key not in self._records:
            raise KeyError(f"Unknown memory: {memory_id}")
        self._inactive.add(key)

    async def activate(self, memory_id: UUID) -> None:
        key = str(memory_id)
        if key not in self._records:
            raise KeyError(f"Unknown memory: {memory_id}")
        self._inactive.discard(key)

    async def search(self, query: MemoryQuery) -> tuple[MemoryRecord, ...]:
        now = datetime.now(UTC)
        query_terms = self._terms(query.text)
        candidates: list[tuple[float, MemoryRecord]] = []

        for memory in self._records.values():
            if str(memory.id) in self._inactive:
                continue
            if memory.tenant_id != query.tenant_id:
                continue
            if memory.agent_id != query.agent_id or memory.namespace != query.namespace:
                continue
            if query.memory_types and memory.memory_type not in query.memory_types:
                continue
            if memory.expires_at is not None and memory.expires_at <= now:
                continue

            memory_terms = self._terms(memory.content)
            overlap = len(query_terms & memory_terms) / max(len(query_terms), 1)
            score = overlap * 0.6 + memory.importance * 0.25 + memory.confidence * 0.15
            candidates.append((score, memory))

        candidates.sort(key=lambda item: (item[0], item[1].created_at), reverse=True)
        return tuple(memory for _, memory in candidates[: query.limit])

    @staticmethod
    def _terms(text: str) -> set[str]:
        return set(re.findall(r"[\w\u4e00-\u9fff]+", text.lower()))
