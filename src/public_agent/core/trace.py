from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from public_agent.core.types import RunStatus


class RunTraceEvent(BaseModel):
    """One immutable event in a persisted run trajectory."""

    model_config = ConfigDict(frozen=True)

    id: UUID
    sequence: int = Field(ge=1)
    event_type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class RunTrace(BaseModel):
    """Complete, ordered trajectory used as evidence for post-run reflection."""

    model_config = ConfigDict(frozen=True)

    run_id: UUID
    tenant_id: str
    agent_id: str
    agent_version: str
    task: str
    status: RunStatus
    output: str | None = None
    error: str | None = None
    events: tuple[RunTraceEvent, ...] = ()
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def validate_event_order(self) -> RunTrace:
        sequences = [event.sequence for event in self.events]
        if sequences != sorted(sequences) or len(sequences) != len(set(sequences)):
            raise ValueError("Run trace events must have unique, ascending sequences")
        event_ids = [event.id for event in self.events]
        if len(event_ids) != len(set(event_ids)):
            raise ValueError("Run trace event ids must be unique")
        return self
