from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Annotated, Literal, Protocol
from uuid import UUID

from fastapi import APIRouter, Depends, FastAPI, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator

from public_agent.api.base import APIError, APIPrincipal
from public_agent.growth.management import (
    CandidateApprovalRecord,
    CandidateDecision,
    CandidateEvaluationRecord,
    CandidateManagementPage,
    CandidateManagementQuery,
    CandidateManagementRecord,
    CandidateStateConflictError,
    GrowthCursorError,
    MemoryManagementPage,
    MemoryManagementQuery,
    MemoryManagementRecord,
    PublishedMemoryRecord,
)
from public_agent.growth.models import CandidateRisk, CandidateStatus, CandidateType
from public_agent.memory.base import MemoryType


class GrowthPrincipal(APIPrincipal):
    """Authenticated server-side identity used for memory and growth governance."""


GrowthPrincipalDependency = Callable[..., GrowthPrincipal | Awaitable[GrowthPrincipal]]


class GrowthManagementService(Protocol):
    async def list_memories(self, query: MemoryManagementQuery) -> MemoryManagementPage: ...

    async def list_candidates(
        self,
        query: CandidateManagementQuery,
    ) -> CandidateManagementPage: ...

    async def get_candidate(
        self,
        *,
        candidate_id: UUID,
        tenant_id: str,
        agent_id: str,
        domain_id: str,
    ) -> CandidateManagementRecord: ...

    async def evaluate_candidate(
        self,
        *,
        candidate_id: UUID,
        tenant_id: str,
        agent_id: str,
        domain_id: str,
        expected_version: int,
    ) -> CandidateManagementRecord: ...

    async def decide_candidate(
        self,
        *,
        candidate_id: UUID,
        tenant_id: str,
        agent_id: str,
        domain_id: str,
        expected_version: int,
        decision: CandidateDecision,
        decided_by: str,
        decision_note: str | None = None,
    ) -> CandidateManagementRecord: ...

    async def rollback_candidate(
        self,
        *,
        candidate_id: UUID,
        tenant_id: str,
        agent_id: str,
        domain_id: str,
        expected_version: int,
    ) -> CandidateManagementRecord: ...


class CandidateScopeRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    agent_id: str = Field(min_length=1, max_length=100)
    domain_id: str = Field(min_length=1, max_length=100)
    expected_version: int = Field(ge=1)

    @field_validator("agent_id", "domain_id")
    @classmethod
    def strip_scope(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("scope fields must not be blank")
        return normalized


class CandidateDecisionRequest(CandidateScopeRequest):
    decision: CandidateDecision
    note: str | None = Field(default=None, max_length=2_000)


class MemoryResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    agent_id: str
    domain_id: str
    namespace: str
    memory_type: MemoryType
    content: str
    status: str
    confidence: float
    importance: float
    candidate_id: UUID | None
    source_run_id: UUID | None
    recall_count: int
    last_recalled_at: datetime | None
    created_at: datetime
    updated_at: datetime
    expires_at: datetime | None

    @classmethod
    def from_record(cls, record: MemoryManagementRecord) -> MemoryResponse:
        return cls(**record.model_dump(exclude={"tenant_id"}))


class MemoryPageResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: tuple[MemoryResponse, ...]
    next_cursor: str | None

    @classmethod
    def from_page(cls, page: MemoryManagementPage) -> MemoryPageResponse:
        return cls(
            items=tuple(MemoryResponse.from_record(item) for item in page.items),
            next_cursor=page.next_cursor,
        )


class CandidateProposalResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    content: str
    namespace: str | None = None
    memory_type: str | None = None
    confidence: float | None = None
    importance: float | None = None
    tags: tuple[str, ...] = ()
    applicability: str | None = None


class CandidateConflictResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    candidate_id: UUID
    kind: str
    score: float
    reason: str
    detector_version: str


class CandidateEvaluationResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    passed: bool
    score: float
    summary: str
    metrics: dict[str, float]
    created_at: datetime

    @classmethod
    def from_record(
        cls,
        record: CandidateEvaluationRecord | None,
    ) -> CandidateEvaluationResponse | None:
        if record is None:
            return None
        return cls(**record.model_dump(exclude={"candidate_version"}))


class CandidateApprovalResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    status: str
    decided_by: str | None
    decision_note: str | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_record(
        cls,
        record: CandidateApprovalRecord | None,
    ) -> CandidateApprovalResponse | None:
        return None if record is None else cls(**record.model_dump())


class PublishedMemoryResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    status: str
    recall_count: int
    last_recalled_at: datetime | None

    @classmethod
    def from_record(
        cls,
        record: PublishedMemoryRecord | None,
    ) -> PublishedMemoryResponse | None:
        return None if record is None else cls(**record.model_dump())


class CandidateSummaryResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    agent_id: str
    domain_id: str
    candidate_type: CandidateType
    risk: CandidateRisk
    title: str
    status: CandidateStatus
    version: int
    content_preview: str
    evidence_run_count: int
    latest_evaluation: CandidateEvaluationResponse | None
    latest_approval: CandidateApprovalResponse | None
    published_memory: PublishedMemoryResponse | None
    created_at: datetime
    updated_at: datetime
    expires_at: datetime | None
    protected_until: datetime | None

    @classmethod
    def from_record(cls, record: CandidateManagementRecord) -> CandidateSummaryResponse:
        candidate = record.candidate
        content = str(candidate.proposed_change.get("content", ""))
        preview = content if len(content) <= 500 else f"{content[:499]}…"
        return cls(
            id=candidate.id,
            agent_id=candidate.agent_id,
            domain_id=candidate.domain_id,
            candidate_type=candidate.candidate_type,
            risk=candidate.risk,
            title=candidate.title,
            status=candidate.status,
            version=candidate.version,
            content_preview=preview,
            evidence_run_count=len(candidate.evidence_run_ids),
            latest_evaluation=CandidateEvaluationResponse.from_record(
                record.latest_evaluation
            ),
            latest_approval=CandidateApprovalResponse.from_record(record.latest_approval),
            published_memory=PublishedMemoryResponse.from_record(record.published_memory),
            created_at=candidate.created_at,
            updated_at=candidate.updated_at,
            expires_at=candidate.expires_at,
            protected_until=candidate.protected_until,
        )


class CandidateDetailResponse(CandidateSummaryResponse):
    fingerprint: str
    proposal: CandidateProposalResponse
    evidence_run_ids: tuple[UUID, ...]
    evidence_event_ids: tuple[UUID, ...]
    conflicts: tuple[CandidateConflictResponse, ...]
    source_candidate_ids: tuple[UUID, ...]

    @classmethod
    def from_record(cls, record: CandidateManagementRecord) -> CandidateDetailResponse:
        summary = CandidateSummaryResponse.from_record(record)
        candidate = record.candidate
        change = candidate.proposed_change
        return cls(
            **summary.model_dump(),
            fingerprint=candidate.fingerprint,
            proposal=CandidateProposalResponse(
                content=str(change.get("content", "")),
                namespace=_optional_text(change.get("namespace")),
                memory_type=_optional_text(change.get("memory_type")),
                confidence=_optional_score(change.get("confidence")),
                importance=_optional_score(change.get("importance")),
                tags=_string_tuple(change.get("tags")),
                applicability=_optional_text(change.get("applicability")),
            ),
            evidence_run_ids=candidate.evidence_run_ids,
            evidence_event_ids=_uuid_tuple(change.get("evidence_event_ids")),
            conflicts=_conflict_responses(change.get("conflict_assessments")),
            source_candidate_ids=_source_candidate_ids(change),
        )


class CandidatePageResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: tuple[CandidateSummaryResponse, ...]
    next_cursor: str | None

    @classmethod
    def from_page(cls, page: CandidateManagementPage) -> CandidatePageResponse:
        return cls(
            items=tuple(CandidateSummaryResponse.from_record(item) for item in page.items),
            next_cursor=page.next_cursor,
        )


def install_growth_routes(
    app: FastAPI,
    *,
    service: GrowthManagementService,
    principal_dependency: GrowthPrincipalDependency,
) -> None:
    router = APIRouter(prefix="/v1", tags=["memory", "growth"])
    principal_depends = Depends(principal_dependency)

    @router.get("/memories", response_model=MemoryPageResponse)
    async def list_memories(
        agent_id: Annotated[str, Query(min_length=1, max_length=100)],
        domain_id: Annotated[str, Query(min_length=1, max_length=100)],
        namespace: Annotated[str | None, Query(min_length=1, max_length=150)] = None,
        memory_type: MemoryType | None = None,
        status: Literal["candidate", "active", "superseded", "expired", "rejected"]
        | None = "active",
        text: Annotated[str | None, Query(min_length=1, max_length=500)] = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        cursor: Annotated[str | None, Query(max_length=500)] = None,
        current: GrowthPrincipal = principal_depends,
    ) -> MemoryPageResponse:
        _require_growth(current, agent_id=agent_id, permission="memories:read")
        try:
            page = await service.list_memories(
                MemoryManagementQuery(
                    tenant_id=current.tenant_id,
                    agent_id=agent_id,
                    domain_id=domain_id,
                    namespace=namespace,
                    memory_type=memory_type,
                    status=status,
                    text=text,
                    limit=limit,
                    cursor=cursor,
                )
            )
        except Exception as exc:
            raise _mapped_error(exc, resource="memory") from None
        return MemoryPageResponse.from_page(page)

    @router.get("/candidates", response_model=CandidatePageResponse)
    async def list_candidates(
        agent_id: Annotated[str, Query(min_length=1, max_length=100)],
        domain_id: Annotated[str, Query(min_length=1, max_length=100)],
        status: CandidateStatus | None = None,
        candidate_type: CandidateType | None = None,
        risk: CandidateRisk | None = None,
        text: Annotated[str | None, Query(min_length=1, max_length=500)] = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        cursor: Annotated[str | None, Query(max_length=500)] = None,
        current: GrowthPrincipal = principal_depends,
    ) -> CandidatePageResponse:
        _require_growth(current, agent_id=agent_id, permission="candidates:read")
        try:
            page = await service.list_candidates(
                CandidateManagementQuery(
                    tenant_id=current.tenant_id,
                    agent_id=agent_id,
                    domain_id=domain_id,
                    status=status,
                    candidate_type=candidate_type,
                    risk=risk,
                    text=text,
                    limit=limit,
                    cursor=cursor,
                )
            )
        except Exception as exc:
            raise _mapped_error(exc, resource="candidate") from None
        return CandidatePageResponse.from_page(page)

    @router.get("/candidates/{candidate_id}", response_model=CandidateDetailResponse)
    async def get_candidate(
        candidate_id: UUID,
        agent_id: Annotated[str, Query(min_length=1, max_length=100)],
        domain_id: Annotated[str, Query(min_length=1, max_length=100)],
        current: GrowthPrincipal = principal_depends,
    ) -> CandidateDetailResponse:
        _require_growth(current, agent_id=agent_id, permission="candidates:read")
        try:
            record = await service.get_candidate(
                candidate_id=candidate_id,
                tenant_id=current.tenant_id,
                agent_id=agent_id,
                domain_id=domain_id,
            )
        except Exception as exc:
            raise _mapped_error(exc, resource="candidate") from None
        return CandidateDetailResponse.from_record(record)

    @router.post(
        "/candidates/{candidate_id}/evaluate",
        response_model=CandidateDetailResponse,
    )
    async def evaluate_candidate(
        candidate_id: UUID,
        body: CandidateScopeRequest,
        current: GrowthPrincipal = principal_depends,
    ) -> CandidateDetailResponse:
        _require_growth(current, agent_id=body.agent_id, permission="candidates:evaluate")
        try:
            record = await service.evaluate_candidate(
                candidate_id=candidate_id,
                tenant_id=current.tenant_id,
                agent_id=body.agent_id,
                domain_id=body.domain_id,
                expected_version=body.expected_version,
            )
        except Exception as exc:
            raise _mapped_error(exc, resource="candidate") from None
        return CandidateDetailResponse.from_record(record)

    @router.post(
        "/candidates/{candidate_id}/decide",
        response_model=CandidateDetailResponse,
    )
    async def decide_candidate(
        candidate_id: UUID,
        body: CandidateDecisionRequest,
        current: GrowthPrincipal = principal_depends,
    ) -> CandidateDetailResponse:
        _require_growth(current, agent_id=body.agent_id, permission="candidates:promote")
        try:
            record = await service.decide_candidate(
                candidate_id=candidate_id,
                tenant_id=current.tenant_id,
                agent_id=body.agent_id,
                domain_id=body.domain_id,
                expected_version=body.expected_version,
                decision=body.decision,
                decided_by=current.subject,
                decision_note=body.note,
            )
        except Exception as exc:
            raise _mapped_error(exc, resource="candidate") from None
        return CandidateDetailResponse.from_record(record)

    @router.post(
        "/candidates/{candidate_id}/rollback",
        response_model=CandidateDetailResponse,
    )
    async def rollback_candidate(
        candidate_id: UUID,
        body: CandidateScopeRequest,
        current: GrowthPrincipal = principal_depends,
    ) -> CandidateDetailResponse:
        _require_growth(current, agent_id=body.agent_id, permission="candidates:promote")
        try:
            record = await service.rollback_candidate(
                candidate_id=candidate_id,
                tenant_id=current.tenant_id,
                agent_id=body.agent_id,
                domain_id=body.domain_id,
                expected_version=body.expected_version,
            )
        except Exception as exc:
            raise _mapped_error(exc, resource="candidate") from None
        return CandidateDetailResponse.from_record(record)

    app.include_router(router)


def _require_growth(principal: APIPrincipal, *, agent_id: str, permission: str) -> None:
    principal.require(
        agent_id=agent_id,
        permission=permission,
        code="growth_forbidden",
        message="The authenticated principal cannot access this memory or candidate scope.",
    )


def _mapped_error(exc: Exception, *, resource: str) -> APIError:
    if isinstance(exc, APIError):
        return exc
    if isinstance(exc, KeyError):
        return APIError(
            status_code=404,
            code=f"{resource}_not_found",
            message=f"The requested {resource} was not found.",
        )
    if isinstance(exc, GrowthCursorError):
        return APIError(
            status_code=400,
            code="invalid_cursor",
            message="The management cursor is invalid.",
        )
    if isinstance(exc, CandidateStateConflictError) or (
        isinstance(exc, (ValueError, RuntimeError))
        and any(
            token in str(exc).lower()
            for token in ("candidate changed", "candidate must", "candidate requires", "conflict")
        )
    ):
        return APIError(
            status_code=409,
            code="candidate_state_conflict",
            message="The candidate cannot be changed from its current state.",
        )
    if isinstance(exc, ValueError):
        return APIError(
            status_code=400,
            code="invalid_growth_request",
            message="The memory or candidate request is invalid.",
        )
    return APIError(
        status_code=500,
        code="growth_internal_error",
        message="The memory or candidate operation could not be completed.",
    )


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_score(value: object) -> float | None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    score = float(value)
    return score if 0 <= score <= 1 else None


def _string_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(str(item) for item in value if isinstance(item, str))


def _uuid_tuple(value: object) -> tuple[UUID, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    parsed: list[UUID] = []
    for item in value:
        try:
            parsed.append(UUID(str(item)))
        except ValueError:
            continue
    return tuple(parsed)


def _conflict_responses(value: object) -> tuple[CandidateConflictResponse, ...]:
    if not isinstance(value, list):
        return ()
    responses: list[CandidateConflictResponse] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        try:
            responses.append(
                CandidateConflictResponse(
                    candidate_id=UUID(str(item["candidate_id"])),
                    kind=str(item["kind"]),
                    score=float(item["score"]),
                    reason=str(item["reason"]),
                    detector_version=str(item["detector_version"]),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return tuple(responses)


def _source_candidate_ids(change: dict[str, object]) -> tuple[UUID, ...]:
    for key in ("merge", "compression"):
        derivation = change.get(key)
        if isinstance(derivation, dict):
            source_ids = _uuid_tuple(derivation.get("source_candidate_ids"))
            if source_ids:
                return source_ids
    return ()
