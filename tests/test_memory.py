import pytest

from public_agent.memory.base import MemoryQuery, MemoryRecord, MemoryType
from public_agent.memory.in_memory import InMemoryMemoryStore


@pytest.mark.asyncio
async def test_memory_isolated_by_tenant_agent_and_namespace() -> None:
    store = InMemoryMemoryStore()
    await store.save(
        MemoryRecord(
            tenant_id="tenant-a",
            agent_id="agent-a",
            namespace="finance",
            memory_type=MemoryType.SEMANTIC,
            content="The preferred tax workflow starts with document validation.",
            importance=0.9,
        )
    )
    await store.save(
        MemoryRecord(
            tenant_id="tenant-b",
            agent_id="agent-a",
            namespace="finance",
            memory_type=MemoryType.SEMANTIC,
            content="Private information from another tenant.",
        )
    )

    results = await store.search(
        MemoryQuery(
            tenant_id="tenant-a",
            agent_id="agent-a",
            namespace="finance",
            text="tax workflow",
        )
    )

    assert len(results) == 1
    assert results[0].tenant_id == "tenant-a"
