from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Protocol

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from public_agent.api.auth import APIKeyAuthenticator, create_bearer_principal_dependency
from public_agent.api.auth_management import (
    AuthenticationManagementService,
    AuthManagementPrincipal,
    AuthManagementPrincipalDependency,
    install_auth_management_routes,
)
from public_agent.api.base import install_api_error_handlers
from public_agent.api.capacity_console import install_capacity_governance_console
from public_agent.api.capacity_governance import (
    CapacityGovernancePrincipal,
    CapacityGovernancePrincipalDependency,
    CapacityGovernanceService,
    install_capacity_governance_routes,
)
from public_agent.api.growth import (
    GrowthManagementService,
    GrowthPrincipal,
    GrowthPrincipalDependency,
    install_growth_routes,
)
from public_agent.api.knowledge import (
    KnowledgeManagementService,
    KnowledgePrincipal,
    KnowledgePrincipalDependency,
    install_knowledge_routes,
)
from public_agent.api.operations import (
    OperationsPrincipal,
    OperationsPrincipalDependency,
    ReflectionJobOperationsService,
    install_operations_routes,
)
from public_agent.api.runs import (
    RunManagementService,
    RunPrincipal,
    RunPrincipalDependency,
    install_run_routes,
)
from public_agent.auth import APITokenCodec
from public_agent.config import Settings
from public_agent.operations.capacity import ReflectionCapacityThresholds
from public_agent.operations.capacity_control import (
    CapacityGovernanceIncidentThresholds,
    CapacityGovernanceKnowledgeQualityRiskThresholds,
)
from public_agent.storage.auth import PostgresAPIKeyService
from public_agent.storage.capacity_control import PostgresReflectionCapacityControl
from public_agent.storage.capacity_governance import PostgresReflectionCapacityGovernance
from public_agent.storage.database import Database
from public_agent.storage.operations import PostgresReflectionJobOperations


class HealthDatabase(Protocol):
    async def ping(self) -> None:
        """Raise when the database is unavailable."""

    async def dispose(self) -> None:
        """Release database resources."""


def create_app(
    *,
    settings: Settings | None = None,
    database: HealthDatabase | None = None,
    knowledge: KnowledgeManagementService | None = None,
    knowledge_principal_dependency: KnowledgePrincipalDependency | None = None,
    runs: RunManagementService | None = None,
    run_principal_dependency: RunPrincipalDependency | None = None,
    growth: GrowthManagementService | None = None,
    growth_principal_dependency: GrowthPrincipalDependency | None = None,
    api_keys: APIKeyAuthenticator | None = None,
    auth_management: AuthenticationManagementService | None = None,
    auth_management_principal_dependency: AuthManagementPrincipalDependency | None = None,
    operations: ReflectionJobOperationsService | None = None,
    operations_principal_dependency: OperationsPrincipalDependency | None = None,
    capacity_governance: CapacityGovernanceService | None = None,
    capacity_governance_principal_dependency: (
        CapacityGovernancePrincipalDependency | None
    ) = None,
) -> FastAPI:
    app_settings = settings or Settings()
    app_database = database or Database(app_settings.database_url)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        await app_database.dispose()

    app = FastAPI(title="public_agent", version="0.1.0", lifespan=lifespan)
    install_api_error_handlers(app)

    @app.get("/health/live")
    async def live() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/ready", response_model=None)
    async def ready() -> dict[str, str] | JSONResponse:
        try:
            await app_database.ping()
        except Exception as exc:
            return JSONResponse(
                status_code=503,
                content={"status": "unavailable", "reason": type(exc).__name__},
            )
        return {"status": "ready"}

    resolved_principal_dependency = knowledge_principal_dependency
    if resolved_principal_dependency is None and api_keys is not None:
        resolved_principal_dependency = create_bearer_principal_dependency(
            api_keys,
            principal_type=KnowledgePrincipal,
        )
    if knowledge is not None and resolved_principal_dependency is not None:
        install_knowledge_routes(
            app,
            service=knowledge,
            principal_dependency=resolved_principal_dependency,
        )

    resolved_run_principal_dependency = run_principal_dependency
    if resolved_run_principal_dependency is None and api_keys is not None:
        resolved_run_principal_dependency = create_bearer_principal_dependency(
            api_keys,
            principal_type=RunPrincipal,
        )
    if runs is not None and resolved_run_principal_dependency is not None:
        install_run_routes(
            app,
            service=runs,
            principal_dependency=resolved_run_principal_dependency,
        )

    resolved_growth_principal_dependency = growth_principal_dependency
    if resolved_growth_principal_dependency is None and api_keys is not None:
        resolved_growth_principal_dependency = create_bearer_principal_dependency(
            api_keys,
            principal_type=GrowthPrincipal,
        )
    if growth is not None and resolved_growth_principal_dependency is not None:
        install_growth_routes(
            app,
            service=growth,
            principal_dependency=resolved_growth_principal_dependency,
        )

    resolved_auth_principal_dependency = auth_management_principal_dependency
    if resolved_auth_principal_dependency is None and api_keys is not None:
        resolved_auth_principal_dependency = create_bearer_principal_dependency(
            api_keys,
            principal_type=AuthManagementPrincipal,
        )
    if auth_management is not None and resolved_auth_principal_dependency is not None:
        install_auth_management_routes(
            app,
            service=auth_management,
            principal_dependency=resolved_auth_principal_dependency,
        )

    resolved_operations_principal_dependency = operations_principal_dependency
    if resolved_operations_principal_dependency is None and api_keys is not None:
        resolved_operations_principal_dependency = create_bearer_principal_dependency(
            api_keys,
            principal_type=OperationsPrincipal,
        )
    if operations is not None and resolved_operations_principal_dependency is not None:
        install_operations_routes(
            app,
            service=operations,
            principal_dependency=resolved_operations_principal_dependency,
        )

    resolved_capacity_principal_dependency = capacity_governance_principal_dependency
    if resolved_capacity_principal_dependency is None and api_keys is not None:
        resolved_capacity_principal_dependency = create_bearer_principal_dependency(
            api_keys,
            principal_type=CapacityGovernancePrincipal,
        )
    if capacity_governance is not None and resolved_capacity_principal_dependency is not None:
        install_capacity_governance_routes(
            app,
            service=capacity_governance,
            principal_dependency=resolved_capacity_principal_dependency,
            default_window_seconds=app_settings.reflection_capacity_policy_window_seconds,
            default_window_minimum_observations=(
                app_settings.reflection_capacity_policy_minimum_observations
            ),
            default_cooldown_seconds=(
                app_settings.reflection_capacity_policy_cooldown_seconds
            ),
        )
        install_capacity_governance_console(app)

    return app


def create_management_app(settings: Settings | None = None) -> FastAPI:
    resolved_settings = settings or Settings()
    resolved_settings.require_management_api_secrets()
    database = Database(resolved_settings.database_url)
    api_keys = PostgresAPIKeyService(
        database.sessions,
        codec=APITokenCodec(resolved_settings.api_token_pepper),
    )
    governance = PostgresReflectionCapacityGovernance(
        database.sessions,
        handler_version=resolved_settings.reflection_handler_version,
    )
    capacity_control = PostgresReflectionCapacityControl(
        database.sessions,
        governance=governance,
        governance_tenant=(
            resolved_settings.reflection_capacity_governance_tenant_id
        ),
        fallback_thresholds=ReflectionCapacityThresholds.from_settings(
            resolved_settings
        ),
        drift_window_seconds=(
            resolved_settings.reflection_capacity_drift_window_seconds
        ),
        drift_minimum_observations=(
            resolved_settings.reflection_capacity_drift_minimum_observations
        ),
        drift_critical_observations=(
            resolved_settings.reflection_capacity_drift_critical_observations
        ),
        drift_maximum_observations=(
            resolved_settings.reflection_capacity_drift_maximum_observations
        ),
        alert_response_warning_seconds=(
            resolved_settings.reflection_capacity_alert_response_warning_seconds
        ),
        alert_response_critical_seconds=(
            resolved_settings.reflection_capacity_alert_response_critical_seconds
        ),
        incident_thresholds=CapacityGovernanceIncidentThresholds(
            audit_window_seconds=(
                resolved_settings.reflection_capacity_incident_audit_window_seconds
            ),
            audit_warning_count=(
                resolved_settings.reflection_capacity_incident_audit_warning_count
            ),
            audit_critical_count=(
                resolved_settings.reflection_capacity_incident_audit_critical_count
            ),
            audit_maximum_events=(
                resolved_settings.reflection_capacity_incident_audit_maximum_events
            ),
            reopen_warning_count=(
                resolved_settings.reflection_capacity_incident_reopen_warning_count
            ),
            reopen_critical_count=(
                resolved_settings.reflection_capacity_incident_reopen_critical_count
            ),
            maximum_alerts=(
                resolved_settings.reflection_capacity_incident_maximum_alerts
            ),
            maximum_incidents=(
                resolved_settings.reflection_capacity_incident_maximum_incidents
            ),
        ),
        knowledge_quality_risk_thresholds=(
            CapacityGovernanceKnowledgeQualityRiskThresholds(
                window_seconds=(
                    resolved_settings.reflection_capacity_knowledge_quality_risk_window_seconds
                ),
                unsafe_warning_count=(
                    resolved_settings.reflection_capacity_knowledge_unsafe_warning_count
                ),
                unsafe_critical_count=(
                    resolved_settings.reflection_capacity_knowledge_unsafe_critical_count
                ),
                degraded_warning_count=(
                    resolved_settings.reflection_capacity_knowledge_degraded_warning_count
                ),
                degraded_critical_count=(
                    resolved_settings.reflection_capacity_knowledge_degraded_critical_count
                ),
                maximum_snapshots=(
                    resolved_settings.reflection_capacity_knowledge_quality_maximum_snapshots
                ),
            )
        ),
        knowledge_quality_maximum_trend_buckets=(
            resolved_settings.reflection_capacity_knowledge_quality_maximum_trend_buckets
        ),
    )
    return create_app(
        settings=resolved_settings,
        database=database,
        api_keys=api_keys,
        auth_management=api_keys,
        operations=PostgresReflectionJobOperations(database.sessions),
        capacity_governance=capacity_control,
    )


app = create_app()
