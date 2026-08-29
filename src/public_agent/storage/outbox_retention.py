from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import delete, exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.sql import Select
from sqlalchemy.sql.elements import ColumnElement

from public_agent.core.types import utc_now
from public_agent.operations.outbox_retention import (
    OutboxRetentionPolicy,
    OutboxRetentionPreview,
    OutboxRetentionReport,
)
from public_agent.storage.models import (
    OutboxJobArchiveModel,
    OutboxJobModel,
    ReflectionJobRetryRequestModel,
)
from public_agent.storage.outbox import REFLECTION_JOB_TYPE

_TERMINAL_STATUSES = ("succeeded", "dead_letter")


class PostgresOutboxRetention:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        handler_version: str,
    ) -> None:
        normalized = handler_version.strip()
        if not normalized or len(normalized) > 64:
            raise ValueError("handler_version must contain 1 to 64 characters")
        self._sessions = sessions
        self.handler_version = normalized

    async def preview(self, policy: OutboxRetentionPolicy) -> OutboxRetentionPreview:
        observed_at = utc_now()
        archive_cutoff = observed_at - timedelta(days=policy.archive_after_days)
        purge_cutoff = observed_at - timedelta(days=policy.purge_after_days)
        async with self._sessions() as session:
            archive_eligible = await _count(
                session,
                self._archive_eligible(archive_cutoff),
            )
            purge_query = self._purge_eligible(purge_cutoff)
            purge_eligible = await _count(
                session,
                purge_query.where(~_has_retry_request()),
            )
            purge_blocked = await _count(
                session,
                purge_query.where(_has_retry_request()),
            )
        return OutboxRetentionPreview(
            observed_at=observed_at,
            handler_version=self.handler_version,
            archive_eligible=archive_eligible,
            purge_eligible=purge_eligible,
            purge_blocked_by_retry_requests=purge_blocked,
        )

    async def maintain(
        self,
        policy: OutboxRetentionPolicy,
        *,
        prune: bool,
    ) -> OutboxRetentionReport:
        before = await self.preview(policy)
        archived_jobs = 0
        purged_jobs = 0
        archive_cutoff = before.observed_at - timedelta(days=policy.archive_after_days)
        purge_cutoff = before.observed_at - timedelta(days=policy.purge_after_days)
        for _ in range(policy.maximum_batches):
            archived = await self._archive_batch(
                cutoff=archive_cutoff,
                batch_size=policy.batch_size,
            )
            archived_jobs += archived
            if archived < policy.batch_size:
                break
        if prune:
            for _ in range(policy.maximum_batches):
                purged = await self._purge_batch(
                    cutoff=purge_cutoff,
                    batch_size=policy.batch_size,
                )
                purged_jobs += purged
                if purged < policy.batch_size:
                    break
        after = await self.preview(policy)
        return OutboxRetentionReport(
            executed=True,
            prune_requested=prune,
            archived_jobs=archived_jobs,
            purged_jobs=purged_jobs,
            before=before,
            after=after,
            policy=policy,
        )

    async def _archive_batch(self, *, cutoff: datetime, batch_size: int) -> int:
        async with self._sessions() as session, session.begin():
            rows = tuple(
                await session.scalars(
                    self._archive_eligible(cutoff)
                    .order_by(OutboxJobModel.completed_at, OutboxJobModel.id)
                    .with_for_update(skip_locked=True)
                    .limit(batch_size)
                )
            )
            for row in rows:
                if row.completed_at is None:
                    continue
                session.add(
                    OutboxJobArchiveModel(
                        id=row.id,
                        completed_at=row.completed_at,
                        tenant_id=row.tenant_id,
                        run_id=row.run_id,
                        job_type=row.job_type,
                        handler_version=row.handler_version,
                        status=row.status,
                        payload=row.payload,
                        result_metadata=row.result_metadata,
                        version=row.version,
                        attempts=row.attempts,
                        attempts_in_cycle=row.attempts_in_cycle,
                        max_attempts=row.max_attempts,
                        available_at=row.available_at,
                        last_error_code=row.last_error_code,
                        last_started_at=row.last_started_at,
                        last_processing_duration_ms=row.last_processing_duration_ms,
                        total_processing_duration_ms=row.total_processing_duration_ms,
                        source_created_at=row.created_at,
                        source_updated_at=row.updated_at,
                    )
                )
            await session.flush()
        return len(rows)

    async def _purge_batch(self, *, cutoff: datetime, batch_size: int) -> int:
        async with self._sessions() as session, session.begin():
            ids = tuple(
                await session.scalars(
                    self._purge_eligible(cutoff)
                    .where(~_has_retry_request())
                    .order_by(OutboxJobModel.completed_at, OutboxJobModel.id)
                    .with_for_update(skip_locked=True)
                    .limit(batch_size)
                    .with_only_columns(OutboxJobModel.id)
                )
            )
            if ids:
                await session.execute(
                    delete(OutboxJobModel).where(OutboxJobModel.id.in_(ids))
                )
        return len(ids)

    def _archive_eligible(self, cutoff: datetime) -> Select[tuple[OutboxJobModel]]:
        return select(OutboxJobModel).where(
            OutboxJobModel.job_type == REFLECTION_JOB_TYPE,
            OutboxJobModel.handler_version == self.handler_version,
            OutboxJobModel.status.in_(_TERMINAL_STATUSES),
            OutboxJobModel.completed_at.is_not(None),
            OutboxJobModel.completed_at <= cutoff,
            ~_has_current_archive(),
        )

    def _purge_eligible(self, cutoff: datetime) -> Select[tuple[OutboxJobModel]]:
        return select(OutboxJobModel).where(
            OutboxJobModel.job_type == REFLECTION_JOB_TYPE,
            OutboxJobModel.handler_version == self.handler_version,
            OutboxJobModel.status.in_(_TERMINAL_STATUSES),
            OutboxJobModel.completed_at.is_not(None),
            OutboxJobModel.completed_at <= cutoff,
            _has_current_archive(),
        )


def _has_current_archive() -> ColumnElement[bool]:
    return exists(
        select(1).where(
            OutboxJobArchiveModel.id == OutboxJobModel.id,
            OutboxJobArchiveModel.completed_at == OutboxJobModel.completed_at,
            OutboxJobArchiveModel.version == OutboxJobModel.version,
        )
    )


def _has_retry_request() -> ColumnElement[bool]:
    return exists(
        select(1).where(
            ReflectionJobRetryRequestModel.job_id == OutboxJobModel.id,
            ReflectionJobRetryRequestModel.tenant_id == OutboxJobModel.tenant_id,
        )
    )


async def _count(session: AsyncSession, statement: Select[Any]) -> int:
    count_statement = select(func.count()).select_from(statement.subquery())
    return int(await session.scalar(count_statement) or 0)
