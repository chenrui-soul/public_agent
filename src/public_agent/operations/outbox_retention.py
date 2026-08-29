from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator


class OutboxRetentionPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    archive_after_days: int = Field(default=7, ge=1, le=3_650)
    purge_after_days: int = Field(default=90, ge=2, le=3_650)
    batch_size: int = Field(default=500, ge=1, le=10_000)
    maximum_batches: int = Field(default=10, ge=1, le=1_000)

    @model_validator(mode="after")
    def validate_retention_windows(self) -> OutboxRetentionPolicy:
        if self.purge_after_days <= self.archive_after_days:
            raise ValueError("purge_after_days must exceed archive_after_days")
        return self


class OutboxRetentionPreview(BaseModel):
    model_config = ConfigDict(frozen=True)

    observed_at: datetime
    handler_version: str
    archive_eligible: int
    purge_eligible: int
    purge_blocked_by_retry_requests: int


class OutboxRetentionReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    executed: bool
    prune_requested: bool
    archived_jobs: int
    purged_jobs: int
    before: OutboxRetentionPreview
    after: OutboxRetentionPreview
    policy: OutboxRetentionPolicy
