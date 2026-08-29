from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Annotated, Any, Protocol
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    FastAPI,
    File,
    Form,
    Header,
    Query,
    UploadFile,
)
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from public_agent.api.base import APIError, APIPrincipal
from public_agent.knowledge.base import (
    KnowledgeDocumentPage,
    KnowledgeDocumentRecord,
    KnowledgeIngestionRecord,
    KnowledgeIngestionStage,
    KnowledgeIngestionStatus,
)
from public_agent.knowledge.errors import (
    KnowledgeCursorError,
    KnowledgeDocumentStateError,
    KnowledgeIdempotencyConflictError,
    KnowledgeNotFoundError,
    KnowledgeStepInProgressError,
    KnowledgeStepOwnershipLostError,
)
from public_agent.knowledge.ingestion import KnowledgeFileInput
from public_agent.knowledge.parsing import (
    MAX_DOCUMENT_BYTES,
    DocumentParseError,
    DocumentSource,
)


class KnowledgePrincipal(APIPrincipal):
    """Authenticated server-side identity used to authorize knowledge management."""


KnowledgePrincipalDependency = Callable[
    ...,
    KnowledgePrincipal | Awaitable[KnowledgePrincipal],
]


class KnowledgeManagementService(Protocol):
    async def create_ingestion(
        self,
        request: KnowledgeFileInput,
        *,
        idempotency_key: str,
    ) -> KnowledgeIngestionRecord: ...

    async def get_ingestion(
        self,
        *,
        job_id: UUID,
        tenant_id: str,
        agent_id: str,
    ) -> KnowledgeIngestionRecord: ...

    async def step_ingestion(
        self,
        *,
        job_id: UUID,
        tenant_id: str,
        agent_id: str,
        batch_size: int = 32,
        lease_seconds: int = 300,
    ) -> KnowledgeIngestionRecord: ...

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
    ) -> KnowledgeDocumentPage: ...

    async def archive_document(
        self,
        *,
        document_id: UUID,
        tenant_id: str,
        agent_id: str,
    ) -> KnowledgeDocumentRecord: ...


KnowledgeAPIError = APIError


class KnowledgeIngestionResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    tenant_id: str
    agent_id: str
    domain_id: str
    namespace: str
    source_key: str
    version: str
    filename: str
    media_type: str
    status: KnowledgeIngestionStatus
    stage: KnowledgeIngestionStage
    processed: int = Field(ge=0)
    total: int = Field(ge=0)
    percent: float = Field(ge=0, le=100)
    has_more: bool
    attempts: int = Field(ge=0)
    document_id: UUID | None = None
    error_code: str | None = None
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_record(cls, record: KnowledgeIngestionRecord) -> KnowledgeIngestionResponse:
        if record.status is KnowledgeIngestionStatus.SUCCEEDED:
            percent = 100.0
        elif record.total_chunks:
            percent = round(record.processed_chunks * 100 / record.total_chunks, 2)
        else:
            percent = 0.0
        return cls(
            id=record.id,
            tenant_id=record.tenant_id,
            agent_id=record.agent_id,
            domain_id=record.domain_id,
            namespace=record.namespace,
            source_key=record.source_key,
            version=record.version,
            filename=record.filename,
            media_type=record.media_type,
            status=record.status,
            stage=record.stage,
            processed=record.processed_chunks,
            total=record.total_chunks,
            percent=percent,
            has_more=record.has_more,
            attempts=record.attempts,
            document_id=record.document_id,
            error_code=record.error_code,
            error_message=record.error_message,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )


class KnowledgeStepRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    agent_id: str = Field(min_length=1, max_length=100)
    batch_size: int = Field(default=32, ge=1, le=128)
    lease_seconds: int = Field(default=300, ge=1, le=3600)


class KnowledgeArchiveRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    agent_id: str = Field(min_length=1, max_length=100)


def install_knowledge_routes(
    app: FastAPI,
    *,
    service: KnowledgeManagementService,
    principal_dependency: KnowledgePrincipalDependency,
) -> None:
    router = APIRouter(prefix="/v1/knowledge", tags=["knowledge"])
    principal_depends = Depends(principal_dependency)

    @router.post(
        "/ingestions",
        response_model=KnowledgeIngestionResponse,
        status_code=202,
    )
    async def create_ingestion(
        idempotency_key: Annotated[
            str,
            Header(alias="Idempotency-Key", min_length=1, max_length=200),
        ],
        agent_id: Annotated[str, Form(min_length=1, max_length=100)],
        domain_id: Annotated[str, Form(min_length=1, max_length=100)],
        namespace: Annotated[str, Form(min_length=1, max_length=150)],
        source_key: Annotated[str, Form(min_length=1, max_length=300)],
        file: Annotated[UploadFile, File()],
        version: Annotated[str, Form(min_length=1, max_length=100)] = "1",
        title: Annotated[str | None, Form(max_length=500)] = None,
        source_uri: Annotated[str | None, Form(max_length=2000)] = None,
        access_tags: Annotated[str, Form()] = "[]",
        metadata: Annotated[str, Form()] = "{}",
        current: KnowledgePrincipal = principal_depends,
    ) -> KnowledgeIngestionResponse:
        _require_knowledge(current, agent_id=agent_id, permission="knowledge:write")
        try:
            content = await _read_upload(file)
            request = KnowledgeFileInput(
                tenant_id=current.tenant_id,
                agent_id=agent_id,
                domain_id=domain_id,
                namespace=namespace,
                source_key=source_key,
                source=DocumentSource(
                    filename=file.filename or "",
                    media_type=file.content_type or "",
                    content=content,
                ),
                title=title or None,
                version=version,
                source_uri=source_uri or None,
                access_tags=_parse_access_tags(access_tags),
                metadata=_parse_metadata(metadata),
            )
            record = await service.create_ingestion(
                request,
                idempotency_key=idempotency_key,
            )
        except Exception as exc:
            raise _mapped_error(exc) from None
        return KnowledgeIngestionResponse.from_record(record)

    @router.post(
        "/ingestions/{ingestion_id}/step",
        response_model=KnowledgeIngestionResponse,
    )
    async def step_ingestion(
        ingestion_id: UUID,
        body: KnowledgeStepRequest,
        current: KnowledgePrincipal = principal_depends,
    ) -> KnowledgeIngestionResponse:
        _require_knowledge(current, agent_id=body.agent_id, permission="knowledge:write")
        try:
            record = await service.step_ingestion(
                job_id=ingestion_id,
                tenant_id=current.tenant_id,
                agent_id=body.agent_id,
                batch_size=body.batch_size,
                lease_seconds=body.lease_seconds,
            )
        except Exception as exc:
            raise _mapped_error(exc) from None
        return KnowledgeIngestionResponse.from_record(record)

    @router.get(
        "/ingestions/{ingestion_id}",
        response_model=KnowledgeIngestionResponse,
    )
    async def get_ingestion(
        ingestion_id: UUID,
        agent_id: Annotated[str, Query(min_length=1, max_length=100)],
        current: KnowledgePrincipal = principal_depends,
    ) -> KnowledgeIngestionResponse:
        _require_knowledge(current, agent_id=agent_id, permission="knowledge:read")
        try:
            record = await service.get_ingestion(
                job_id=ingestion_id,
                tenant_id=current.tenant_id,
                agent_id=agent_id,
            )
        except Exception as exc:
            raise _mapped_error(exc) from None
        return KnowledgeIngestionResponse.from_record(record)

    @router.get("/documents", response_model=KnowledgeDocumentPage)
    async def list_documents(
        agent_id: Annotated[str, Query(min_length=1, max_length=100)],
        domain_id: Annotated[str, Query(min_length=1, max_length=100)],
        namespace: Annotated[str | None, Query(min_length=1, max_length=150)] = None,
        status: Annotated[str | None, Query()] = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        cursor: Annotated[str | None, Query(max_length=500)] = None,
        current: KnowledgePrincipal = principal_depends,
    ) -> KnowledgeDocumentPage:
        _require_knowledge(current, agent_id=agent_id, permission="knowledge:read")
        try:
            return await service.list_documents(
                tenant_id=current.tenant_id,
                agent_id=agent_id,
                domain_id=domain_id,
                namespace=namespace,
                status=status,
                limit=limit,
                cursor=cursor,
            )
        except Exception as exc:
            raise _mapped_error(exc) from None

    @router.post(
        "/documents/{document_id}/archive",
        response_model=KnowledgeDocumentRecord,
    )
    async def archive_document(
        document_id: UUID,
        body: KnowledgeArchiveRequest,
        current: KnowledgePrincipal = principal_depends,
    ) -> KnowledgeDocumentRecord:
        _require_knowledge(current, agent_id=body.agent_id, permission="knowledge:write")
        try:
            return await service.archive_document(
                document_id=document_id,
                tenant_id=current.tenant_id,
                agent_id=body.agent_id,
            )
        except Exception as exc:
            raise _mapped_error(exc) from None

    app.include_router(router)


def _require_knowledge(
    principal: APIPrincipal,
    *,
    agent_id: str,
    permission: str,
) -> None:
    principal.require(
        agent_id=agent_id,
        permission=permission,
        code="knowledge_forbidden",
        message="The authenticated principal cannot access this knowledge scope.",
    )


async def _read_upload(file: UploadFile) -> bytes:
    try:
        content = await file.read(MAX_DOCUMENT_BYTES + 1)
    finally:
        await file.close()
    if len(content) > MAX_DOCUMENT_BYTES:
        raise KnowledgeAPIError(
            status_code=413,
            code="file_too_large",
            message="The uploaded document exceeds the configured byte limit.",
        )
    return content


def _parse_access_tags(value: str) -> tuple[str, ...]:
    parsed = _parse_json(value, field="access_tags")
    if not isinstance(parsed, list) or not all(isinstance(item, str) for item in parsed):
        raise KnowledgeAPIError(
            status_code=422,
            code="invalid_access_tags",
            message="access_tags must be a JSON array of strings.",
        )
    return tuple(parsed)


def _parse_metadata(value: str) -> dict[str, Any]:
    parsed = _parse_json(value, field="metadata")
    if not isinstance(parsed, dict):
        raise KnowledgeAPIError(
            status_code=422,
            code="invalid_metadata",
            message="metadata must be a JSON object.",
        )
    return parsed


def _parse_json(value: str, *, field: str) -> object:
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        raise KnowledgeAPIError(
            status_code=422,
            code=f"invalid_{field}",
            message=f"{field} must contain valid JSON.",
        ) from None


def _mapped_error(exc: Exception) -> KnowledgeAPIError:
    if isinstance(exc, KnowledgeAPIError):
        return exc
    if isinstance(exc, DocumentParseError):
        return KnowledgeAPIError(status_code=400, code=exc.code, message=str(exc))
    if isinstance(exc, ValidationError):
        return KnowledgeAPIError(
            status_code=422,
            code="request_validation_failed",
            message="Request validation failed.",
        )
    if isinstance(exc, KnowledgeNotFoundError) or isinstance(exc, KeyError):
        return KnowledgeAPIError(
            status_code=404,
            code="knowledge_not_found",
            message="The requested knowledge resource was not found.",
        )
    if isinstance(exc, KnowledgeStepInProgressError):
        return KnowledgeAPIError(
            status_code=409,
            code="ingestion_step_in_progress",
            message="Another worker is already processing this ingestion.",
        )
    if isinstance(exc, KnowledgeStepOwnershipLostError):
        return KnowledgeAPIError(
            status_code=409,
            code="ingestion_step_ownership_lost",
            message="The ingestion step lease is no longer owned by this worker.",
        )
    if isinstance(exc, KnowledgeIdempotencyConflictError):
        return KnowledgeAPIError(
            status_code=409,
            code="idempotency_conflict",
            message="The idempotency key is bound to a different request.",
        )
    if isinstance(exc, KnowledgeDocumentStateError):
        return KnowledgeAPIError(
            status_code=409,
            code="invalid_document_state",
            message="Only an active knowledge document can be archived.",
        )
    if isinstance(exc, KnowledgeCursorError):
        return KnowledgeAPIError(
            status_code=400,
            code="invalid_cursor",
            message="The knowledge document cursor is invalid.",
        )
    if isinstance(exc, ValueError):
        return KnowledgeAPIError(
            status_code=400,
            code="invalid_knowledge_request",
            message="The knowledge request is invalid.",
        )
    return KnowledgeAPIError(
        status_code=500,
        code="knowledge_internal_error",
        message="The knowledge operation could not be completed.",
    )
