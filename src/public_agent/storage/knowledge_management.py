from __future__ import annotations

import base64
import binascii
import hashlib
import json
import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import PurePath
from uuid import UUID, uuid4

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from public_agent.core.types import utc_now
from public_agent.knowledge.base import (
    KNOWLEDGE_EMBEDDING_DIMENSIONS,
    EmbeddingProvider,
    KnowledgeDocumentInput,
    KnowledgeDocumentPage,
    KnowledgeDocumentRecord,
    KnowledgeIngestionRecord,
    KnowledgeIngestionStage,
    KnowledgeIngestionStatus,
    KnowledgeWriter,
    PreparedKnowledgeChunk,
    PreparedKnowledgeDocument,
)
from public_agent.knowledge.chunking import TextChunker
from public_agent.knowledge.errors import (
    KnowledgeCursorError,
    KnowledgeDocumentStateError,
    KnowledgeIdempotencyConflictError,
    KnowledgeNotFoundError,
    KnowledgeStepInProgressError,
    KnowledgeStepOwnershipLostError,
)
from public_agent.knowledge.ingestion import KnowledgeFileInput
from public_agent.knowledge.parsing import DocumentParseError, DocumentParser, DocumentSource
from public_agent.storage.models import (
    AgentModel,
    KnowledgeChunkModel,
    KnowledgeDocumentModel,
    KnowledgeIngestionChunkModel,
    KnowledgeIngestionJobModel,
    TenantModel,
)


@dataclass(frozen=True, slots=True)
class _KnowledgeScope:
    tenant_id: UUID
    agent_id: UUID
    domain_id: str


@dataclass(frozen=True, slots=True)
class _StepClaim:
    job_id: UUID
    tenant_slug: str
    agent_key: str
    stage: KnowledgeIngestionStage
    token: UUID
    lease_expires_at: datetime


class PostgresKnowledgeManagementService:
    def __init__(
        self,
        *,
        sessions: async_sessionmaker[AsyncSession],
        writer: KnowledgeWriter,
        embeddings: EmbeddingProvider,
        parser: DocumentParser | None = None,
        chunker: TextChunker | None = None,
        max_chunks: int = 500,
    ) -> None:
        if embeddings.profile.dimensions != KNOWLEDGE_EMBEDDING_DIMENSIONS:
            raise ValueError(
                "knowledge management embeddings must match the PostgreSQL dimensions"
            )
        if not 1 <= max_chunks <= 5_000:
            raise ValueError("max_chunks must be between 1 and 5000")
        self._sessions = sessions
        self._writer = writer
        self._embeddings = embeddings
        self._parser = parser or DocumentParser()
        self._chunker = chunker or TextChunker()
        self._max_chunks = max_chunks

    async def create_ingestion(
        self,
        request: KnowledgeFileInput,
        *,
        idempotency_key: str,
    ) -> KnowledgeIngestionRecord:
        key = idempotency_key.strip()
        if not key or len(key) > 200:
            raise ValueError("idempotency_key must contain 1 to 200 characters")
        try:
            json.dumps(request.metadata, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ValueError("knowledge ingestion metadata must be JSON serializable") from exc
        self._parser.validate_source(request.source)
        source_hash = hashlib.sha256(request.source.content).hexdigest()
        request_hash = _request_hash(request, source_hash=source_hash)
        async with self._sessions() as session, session.begin():
            scope = await _resolve_scope(session, request.tenant_id, request.agent_id)
            if request.domain_id != scope.domain_id:
                raise ValueError("knowledge domain does not match the registered agent domain")
            await session.execute(
                select(
                    func.pg_advisory_xact_lock(
                        _idempotency_lock_id(scope=scope, idempotency_key=key)
                    )
                )
            )
            existing = await session.scalar(
                select(KnowledgeIngestionJobModel).where(
                    KnowledgeIngestionJobModel.tenant_id == scope.tenant_id,
                    KnowledgeIngestionJobModel.agent_id == scope.agent_id,
                    KnowledgeIngestionJobModel.idempotency_key == key,
                )
            )
            if existing is not None:
                if existing.request_hash != request_hash:
                    raise KnowledgeIdempotencyConflictError(
                        "idempotency key is already bound to a different ingestion request"
                    )
                return _ingestion_record(
                    existing,
                    tenant_id=request.tenant_id,
                    agent_id=request.agent_id,
                )
            row = KnowledgeIngestionJobModel(
                id=uuid4(),
                tenant_id=scope.tenant_id,
                agent_id=scope.agent_id,
                domain_id=request.domain_id,
                namespace=request.namespace,
                source_key=request.source_key,
                title=request.title,
                version=request.version,
                source_uri=request.source_uri,
                filename=request.source.filename,
                media_type=request.source.media_type,
                source_bytes=request.source.content,
                source_hash=source_hash,
                request_hash=request_hash,
                idempotency_key=key,
                status=KnowledgeIngestionStatus.QUEUED.value,
                stage=KnowledgeIngestionStage.PARSING.value,
                access_tags=list(request.access_tags),
                metadata_json=request.metadata,
            )
            session.add(row)
            await session.flush()
            return _ingestion_record(
                row,
                tenant_id=request.tenant_id,
                agent_id=request.agent_id,
            )

    async def get_ingestion(
        self,
        *,
        job_id: UUID,
        tenant_id: str,
        agent_id: str,
    ) -> KnowledgeIngestionRecord:
        async with self._sessions() as session:
            scope = await _resolve_scope(session, tenant_id, agent_id)
            row = await session.scalar(
                select(KnowledgeIngestionJobModel).where(
                    KnowledgeIngestionJobModel.id == job_id,
                    KnowledgeIngestionJobModel.tenant_id == scope.tenant_id,
                    KnowledgeIngestionJobModel.agent_id == scope.agent_id,
                )
            )
            if row is None:
                raise KnowledgeNotFoundError(
                    "Unknown knowledge ingestion in requested scope"
                )
            return _ingestion_record(row, tenant_id=tenant_id, agent_id=agent_id)

    async def step_ingestion(
        self,
        *,
        job_id: UUID,
        tenant_id: str,
        agent_id: str,
        batch_size: int = 32,
        lease_seconds: int = 300,
    ) -> KnowledgeIngestionRecord:
        if not 1 <= batch_size <= 128:
            raise ValueError("batch_size must be between 1 and 128")
        if not 1 <= lease_seconds <= 3_600:
            raise ValueError("lease_seconds must be between 1 and 3600")
        claim, replayed = await self._claim_step(
            job_id=job_id,
            tenant_id=tenant_id,
            agent_id=agent_id,
            lease_seconds=lease_seconds,
        )
        if replayed is not None:
            return replayed
        if claim is None:
            raise RuntimeError("knowledge ingestion claim is missing")
        try:
            if claim.stage is KnowledgeIngestionStage.PARSING:
                await self._parse_step(claim)
            elif claim.stage is KnowledgeIngestionStage.EMBEDDING:
                await self._embedding_step(claim, batch_size=batch_size)
            elif claim.stage is KnowledgeIngestionStage.PUBLISHING:
                await self._publish_step(claim)
            else:
                raise ValueError("knowledge ingestion has an invalid active stage")
        except KnowledgeStepOwnershipLostError:
            raise
        except DocumentParseError as exc:
            return await self._fail_step(claim, code=exc.code, message=str(exc))
        except Exception as exc:
            return await self._fail_step(
                claim,
                code="ingestion_step_failed",
                message=f"Knowledge ingestion step failed: {type(exc).__name__}",
            )
        return await self.get_ingestion(
            job_id=job_id,
            tenant_id=tenant_id,
            agent_id=agent_id,
        )

    async def list_documents(
        self,
        *,
        tenant_id: str,
        agent_id: str,
        domain_id: str,
        namespace: str | None = None,
        status: str | None = None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> KnowledgeDocumentPage:
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        if status is not None and status not in {"active", "superseded", "archived"}:
            raise ValueError("invalid knowledge document status")
        after = _decode_cursor(cursor) if cursor is not None else None
        async with self._sessions() as session:
            scope = await _resolve_scope(session, tenant_id, agent_id)
            if domain_id != scope.domain_id:
                raise ValueError("knowledge domain does not match the registered agent domain")
            filters = [
                KnowledgeDocumentModel.tenant_id == scope.tenant_id,
                KnowledgeDocumentModel.agent_id == scope.agent_id,
                KnowledgeDocumentModel.domain_id == domain_id,
            ]
            if namespace is not None:
                filters.append(KnowledgeDocumentModel.namespace == namespace)
            if status is not None:
                filters.append(KnowledgeDocumentModel.status == status)
            if after is not None:
                filters.append(
                    or_(
                        KnowledgeDocumentModel.created_at < after[0],
                        and_(
                            KnowledgeDocumentModel.created_at == after[0],
                            KnowledgeDocumentModel.id < after[1],
                        ),
                    )
                )
            rows = (
                await session.execute(
                    select(KnowledgeDocumentModel, func.count(KnowledgeChunkModel.id))
                    .outerjoin(
                        KnowledgeChunkModel,
                        KnowledgeChunkModel.document_id == KnowledgeDocumentModel.id,
                    )
                    .where(*filters)
                    .group_by(KnowledgeDocumentModel.id)
                    .order_by(
                        KnowledgeDocumentModel.created_at.desc(),
                        KnowledgeDocumentModel.id.desc(),
                    )
                    .limit(limit + 1)
                )
            ).all()
        selected = rows[:limit]
        items = tuple(
            _document_record(
                row,
                tenant_id=tenant_id,
                agent_id=agent_id,
                chunk_count=int(chunk_count),
            )
            for row, chunk_count in selected
        )
        next_cursor = None
        if len(rows) > limit and selected:
            last = selected[-1][0]
            next_cursor = _encode_cursor(last.created_at, last.id)
        return KnowledgeDocumentPage(items=items, next_cursor=next_cursor)

    async def archive_document(
        self,
        *,
        document_id: UUID,
        tenant_id: str,
        agent_id: str,
    ) -> KnowledgeDocumentRecord:
        async with self._sessions() as session, session.begin():
            scope = await _resolve_scope(session, tenant_id, agent_id)
            row = await session.scalar(
                select(KnowledgeDocumentModel)
                .where(
                    KnowledgeDocumentModel.id == document_id,
                    KnowledgeDocumentModel.tenant_id == scope.tenant_id,
                    KnowledgeDocumentModel.agent_id == scope.agent_id,
                )
                .with_for_update()
            )
            if row is None:
                raise KnowledgeNotFoundError("Unknown knowledge document in requested scope")
            if row.status == "active":
                row.status = "archived"
            elif row.status != "archived":
                raise KnowledgeDocumentStateError(
                    "only an active knowledge document can be archived"
                )
            chunk_count = await session.scalar(
                select(func.count(KnowledgeChunkModel.id)).where(
                    KnowledgeChunkModel.document_id == row.id
                )
            )
            return _document_record(
                row,
                tenant_id=tenant_id,
                agent_id=agent_id,
                chunk_count=int(chunk_count or 0),
            )

    async def _claim_step(
        self,
        *,
        job_id: UUID,
        tenant_id: str,
        agent_id: str,
        lease_seconds: int,
    ) -> tuple[_StepClaim | None, KnowledgeIngestionRecord | None]:
        async with self._sessions() as session, session.begin():
            scope = await _resolve_scope(session, tenant_id, agent_id)
            row = await session.scalar(
                select(KnowledgeIngestionJobModel)
                .where(
                    KnowledgeIngestionJobModel.id == job_id,
                    KnowledgeIngestionJobModel.tenant_id == scope.tenant_id,
                    KnowledgeIngestionJobModel.agent_id == scope.agent_id,
                )
                .with_for_update()
            )
            if row is None:
                raise KnowledgeNotFoundError(
                    "Unknown knowledge ingestion in requested scope"
                )
            if row.status in {
                KnowledgeIngestionStatus.SUCCEEDED.value,
                KnowledgeIngestionStatus.FAILED.value,
                KnowledgeIngestionStatus.CANCELED.value,
            }:
                return None, _ingestion_record(row, tenant_id=tenant_id, agent_id=agent_id)
            now = utc_now()
            if (
                row.status == KnowledgeIngestionStatus.RUNNING.value
                and row.step_token is not None
                and row.step_lease_expires_at is not None
                and row.step_lease_expires_at > now
            ):
                raise KnowledgeStepInProgressError(
                    "knowledge ingestion step is already in progress"
                )
            token = uuid4()
            lease_expires_at = now + timedelta(seconds=lease_seconds)
            row.status = KnowledgeIngestionStatus.RUNNING.value
            row.step_token = token
            row.step_lease_expires_at = lease_expires_at
            row.attempts += 1
            row.error_code = None
            row.error_message = None
            return (
                _StepClaim(
                    job_id=row.id,
                    tenant_slug=tenant_id,
                    agent_key=agent_id,
                    stage=KnowledgeIngestionStage(row.stage),
                    token=token,
                    lease_expires_at=lease_expires_at,
                ),
                None,
            )

    async def _parse_step(self, claim: _StepClaim) -> None:
        async with self._sessions() as session:
            row = await session.get(KnowledgeIngestionJobModel, claim.job_id)
            if row is None or row.source_bytes is None:
                raise ValueError("knowledge ingestion source is missing")
            source = row.source_bytes
            filename = row.filename
            media_type = row.media_type
        parsed = self._parser.parse(
            DocumentSource(filename=filename, media_type=media_type, content=source)
        )
        chunks = self._chunker.chunk(parsed.text, max_chunks=self._max_chunks)
        async with self._sessions() as session, session.begin():
            row = await self._owned_job(session, claim)
            existing_count = await session.scalar(
                select(func.count(KnowledgeIngestionChunkModel.id)).where(
                    KnowledgeIngestionChunkModel.job_id == row.id
                )
            )
            if existing_count:
                raise ValueError("knowledge ingestion parsing stage already has chunks")
            for chunk in chunks:
                session.add(
                    KnowledgeIngestionChunkModel(
                        id=uuid4(),
                        job_id=row.id,
                        chunk_index=chunk.chunk_index,
                        content=chunk.content,
                        start_char=chunk.start_char,
                        end_char=chunk.end_char,
                    )
                )
            row.parsed_text = parsed.text
            row.parser_metadata = {
                **parsed.metadata,
                "filename": parsed.filename,
                "media_type": parsed.media_type,
                "source_hash": parsed.source_hash,
                "parser_profile": parsed.parser_profile,
                "title": parsed.title,
            }
            row.source_bytes = None
            row.total_chunks = len(chunks)
            row.processed_chunks = 0
            row.stage = KnowledgeIngestionStage.EMBEDDING.value
            _release_step(row)

    async def _embedding_step(self, claim: _StepClaim, *, batch_size: int) -> None:
        async with self._sessions() as session:
            chunks = tuple(
                (
                    await session.scalars(
                        select(KnowledgeIngestionChunkModel)
                        .where(
                            KnowledgeIngestionChunkModel.job_id == claim.job_id,
                            KnowledgeIngestionChunkModel.embedding.is_(None),
                        )
                        .order_by(KnowledgeIngestionChunkModel.chunk_index)
                        .limit(batch_size)
                    )
                ).all()
            )
        if not chunks:
            await self._advance_to_publishing(claim)
            return
        vectors = await self._embeddings.embed_many(tuple(chunk.content for chunk in chunks))
        if len(vectors) != len(chunks):
            raise ValueError("embedding provider returned an unexpected vector count")
        for vector in vectors:
            if len(vector) != KNOWLEDGE_EMBEDDING_DIMENSIONS:
                raise ValueError("embedding dimensions do not match the ingestion index")
            if not all(math.isfinite(value) for value in vector):
                raise ValueError("embedding contains a non-finite value")
        async with self._sessions() as session, session.begin():
            row = await self._owned_job(session, claim)
            for chunk, vector in zip(chunks, vectors, strict=True):
                staged = await session.scalar(
                    select(KnowledgeIngestionChunkModel)
                    .where(
                        KnowledgeIngestionChunkModel.id == chunk.id,
                        KnowledgeIngestionChunkModel.job_id == row.id,
                    )
                    .with_for_update()
                )
                if staged is None:
                    raise ValueError("knowledge ingestion chunk disappeared")
                if staged.embedding is None:
                    staged.embedding = list(vector)
            processed = await session.scalar(
                select(func.count(KnowledgeIngestionChunkModel.id)).where(
                    KnowledgeIngestionChunkModel.job_id == row.id,
                    KnowledgeIngestionChunkModel.embedding.is_not(None),
                )
            )
            row.processed_chunks = int(processed or 0)
            if row.processed_chunks == row.total_chunks:
                row.stage = KnowledgeIngestionStage.PUBLISHING.value
            _release_step(row)

    async def _advance_to_publishing(self, claim: _StepClaim) -> None:
        async with self._sessions() as session, session.begin():
            row = await self._owned_job(session, claim)
            if row.processed_chunks != row.total_chunks:
                raise ValueError("knowledge ingestion progress is incomplete")
            row.stage = KnowledgeIngestionStage.PUBLISHING.value
            _release_step(row)

    async def _publish_step(self, claim: _StepClaim) -> None:
        async with self._sessions() as session:
            row = await session.get(KnowledgeIngestionJobModel, claim.job_id)
            if row is None or row.parsed_text is None:
                raise ValueError("knowledge ingestion parsed content is missing")
            chunks = tuple(
                (
                    await session.scalars(
                        select(KnowledgeIngestionChunkModel)
                        .where(KnowledgeIngestionChunkModel.job_id == row.id)
                        .order_by(KnowledgeIngestionChunkModel.chunk_index)
                    )
                ).all()
            )
            if not chunks or any(chunk.embedding is None for chunk in chunks):
                raise ValueError("knowledge ingestion embeddings are incomplete")
            parser_metadata = dict(row.parser_metadata)
            parser_title = parser_metadata.get("title")
            title = row.title or (
                parser_title
                if isinstance(parser_title, str) and parser_title.strip()
                else PurePath(row.filename).stem
            )
            document = KnowledgeDocumentInput(
                tenant_id=claim.tenant_slug,
                agent_id=claim.agent_key,
                domain_id=row.domain_id,
                namespace=row.namespace,
                source_key=row.source_key,
                title=str(title),
                content=row.parsed_text,
                version=row.version,
                source_uri=row.source_uri,
                access_tags=tuple(row.access_tags),
                metadata={**row.metadata_json, "document_parser": parser_metadata},
            )
            prepared = PreparedKnowledgeDocument(
                document=document,
                content_hash=hashlib.sha256(row.parsed_text.encode("utf-8")).hexdigest(),
                embedding_profile=self._embeddings.profile,
                chunks=tuple(
                    PreparedKnowledgeChunk(
                        chunk_index=chunk.chunk_index,
                        content=chunk.content,
                        start_char=chunk.start_char,
                        end_char=chunk.end_char,
                        embedding=tuple(chunk.embedding or ()),
                    )
                    for chunk in chunks
                ),
            )
        published = await self._writer.publish(prepared)
        async with self._sessions() as session, session.begin():
            row = await self._owned_job(session, claim)
            row.document_id = published.id
            row.status = KnowledgeIngestionStatus.SUCCEEDED.value
            row.stage = KnowledgeIngestionStage.COMPLETED.value
            row.error_code = None
            row.error_message = None
            row.step_token = None
            row.step_lease_expires_at = None

    async def _fail_step(
        self,
        claim: _StepClaim,
        *,
        code: str,
        message: str,
    ) -> KnowledgeIngestionRecord:
        async with self._sessions() as session, session.begin():
            row = await self._owned_job(session, claim)
            row.status = KnowledgeIngestionStatus.FAILED.value
            row.error_code = code[:100]
            row.error_message = message[:500]
            row.source_bytes = None
            row.step_token = None
            row.step_lease_expires_at = None
            await session.flush()
            await session.refresh(row, attribute_names=["updated_at"])
            return _ingestion_record(
                row,
                tenant_id=claim.tenant_slug,
                agent_id=claim.agent_key,
            )

    async def _owned_job(
        self,
        session: AsyncSession,
        claim: _StepClaim,
    ) -> KnowledgeIngestionJobModel:
        row = await session.scalar(
            select(KnowledgeIngestionJobModel)
            .where(KnowledgeIngestionJobModel.id == claim.job_id)
            .with_for_update()
        )
        if row is None:
            raise KnowledgeNotFoundError("Unknown knowledge ingestion")
        if row.step_token != claim.token:
            raise KnowledgeStepOwnershipLostError(
                "knowledge ingestion step token is stale"
            )
        if row.step_lease_expires_at is None or row.step_lease_expires_at <= utc_now():
            raise KnowledgeStepOwnershipLostError(
                "knowledge ingestion step lease expired"
            )
        return row


async def _resolve_scope(
    session: AsyncSession,
    tenant_slug: str,
    agent_key: str,
) -> _KnowledgeScope:
    tenant = await session.scalar(select(TenantModel).where(TenantModel.slug == tenant_slug))
    if tenant is None:
        raise KeyError(f"Unknown tenant: {tenant_slug}")
    agent = await session.scalar(
        select(AgentModel).where(
            AgentModel.tenant_id == tenant.id,
            AgentModel.agent_key == agent_key,
        )
    )
    if agent is None:
        raise KeyError(f"Unknown agent for tenant {tenant_slug}: {agent_key}")
    return _KnowledgeScope(tenant_id=tenant.id, agent_id=agent.id, domain_id=agent.domain_id)


def _release_step(row: KnowledgeIngestionJobModel) -> None:
    row.status = KnowledgeIngestionStatus.QUEUED.value
    row.step_token = None
    row.step_lease_expires_at = None


def _request_hash(request: KnowledgeFileInput, *, source_hash: str) -> str:
    payload = {
        "tenant_id": request.tenant_id,
        "agent_id": request.agent_id,
        "domain_id": request.domain_id,
        "namespace": request.namespace,
        "source_key": request.source_key,
        "title": request.title,
        "version": request.version,
        "source_uri": request.source_uri,
        "access_tags": request.access_tags,
        "metadata": request.metadata,
        "filename": request.source.filename,
        "media_type": request.source.media_type,
        "source_hash": source_hash,
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _idempotency_lock_id(*, scope: _KnowledgeScope, idempotency_key: str) -> int:
    payload = f"{scope.tenant_id}|{scope.agent_id}|{idempotency_key}"
    digest = hashlib.sha256(payload.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=True)


def _ingestion_record(
    row: KnowledgeIngestionJobModel,
    *,
    tenant_id: str,
    agent_id: str,
) -> KnowledgeIngestionRecord:
    return KnowledgeIngestionRecord(
        id=row.id,
        tenant_id=tenant_id,
        agent_id=agent_id,
        domain_id=row.domain_id,
        namespace=row.namespace,
        source_key=row.source_key,
        version=row.version,
        filename=row.filename,
        media_type=row.media_type,
        status=KnowledgeIngestionStatus(row.status),
        stage=KnowledgeIngestionStage(row.stage),
        processed_chunks=row.processed_chunks,
        total_chunks=row.total_chunks,
        attempts=row.attempts,
        document_id=row.document_id,
        error_code=row.error_code,
        error_message=row.error_message,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _document_record(
    row: KnowledgeDocumentModel,
    *,
    tenant_id: str,
    agent_id: str,
    chunk_count: int,
) -> KnowledgeDocumentRecord:
    return KnowledgeDocumentRecord(
        id=row.id,
        tenant_id=tenant_id,
        agent_id=agent_id,
        domain_id=row.domain_id,
        namespace=row.namespace,
        source_key=row.source_key,
        title=row.title,
        version=row.version,
        content_hash=row.content_hash,
        chunk_count=chunk_count,
        status=row.status,
        source_uri=row.source_uri,
        access_tags=tuple(row.access_tags),
        metadata=row.metadata_json,
        created_at=row.created_at,
    )


def _encode_cursor(created_at: datetime, document_id: UUID) -> str:
    payload = json.dumps(
        {"created_at": created_at.isoformat(), "id": str(document_id)},
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _decode_cursor(value: str) -> tuple[datetime, UUID]:
    if not value or len(value) > 500:
        raise KnowledgeCursorError("invalid knowledge document cursor")
    try:
        padding = "=" * (-len(value) % 4)
        encoded = (value + padding).encode("ascii")
        decoded = base64.b64decode(encoded, altchars=b"-_", validate=True)
        payload = json.loads(decoded.decode("utf-8"))
        if not isinstance(payload, dict) or set(payload) != {"created_at", "id"}:
            raise TypeError
        created_at = datetime.fromisoformat(payload["created_at"])
        document_id = UUID(payload["id"])
        if created_at.tzinfo is None:
            raise ValueError
    except (
        binascii.Error,
        KeyError,
        TypeError,
        UnicodeDecodeError,
        ValueError,
        json.JSONDecodeError,
    ) as exc:
        raise KnowledgeCursorError("invalid knowledge document cursor") from exc
    return created_at, document_id
