from __future__ import annotations

import os

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="PUBLIC_AGENT_",
        secrets_dir=os.getenv("PUBLIC_AGENT_SECRETS_DIR"),
        extra="ignore",
    )

    environment: str = "development"
    database_url: str = (
        "postgresql+asyncpg://public_agent:public_agent@localhost:55432/public_agent"
    )
    redis_url: str = "redis://localhost:56379/0"
    log_level: str = "INFO"
    secret_key: SecretStr = SecretStr("development-only-secret")
    api_token_pepper: SecretStr = SecretStr("development-only-token-pepper")
    default_max_steps: int = Field(default=12, ge=1, le=100)
    openai_api_key: SecretStr | None = None
    openai_model: str = Field(default="gpt-5.6-terra", min_length=1, max_length=100)
    openai_max_output_tokens: int = Field(default=4096, ge=1, le=128000)
    openai_timeout_seconds: float = Field(default=60.0, gt=0, le=600)
    openai_max_retries: int = Field(default=2, ge=0, le=5)
    openai_retry_backoff_seconds: float = Field(default=0.25, ge=0, le=5)
    openai_embedding_model: str = Field(
        default="text-embedding-3-small",
        min_length=1,
        max_length=100,
    )
    openai_embedding_dimensions: int = Field(default=384, ge=1, le=3072)
    openai_embedding_timeout_seconds: float = Field(default=15.0, gt=0, le=300)
    openai_embedding_max_retries: int = Field(default=2, ge=0, le=5)
    openai_embedding_batch_size: int = Field(default=128, ge=1, le=2048)
    reflection_handler_version: str = Field(
        default="reflection-v1",
        min_length=1,
        max_length=64,
    )
    reflection_worker_id: str | None = Field(default=None, min_length=1, max_length=200)
    reflection_worker_max_attempts: int = Field(default=5, ge=1, le=100)
    reflection_worker_retry_base_seconds: int = Field(default=30, ge=1, le=86_400)
    reflection_worker_retry_max_seconds: int = Field(default=3_600, ge=1, le=86_400)
    reflection_worker_lease_seconds: int = Field(default=300, ge=5, le=3_600)
    reflection_worker_heartbeat_seconds: int = Field(default=60, ge=1, le=3_599)
    reflection_worker_poll_interval_seconds: float = Field(default=1.0, ge=0.05, le=60)
    reflection_worker_poll_jitter_seconds: float = Field(default=0.25, ge=0, le=60)
    reflection_worker_drain_timeout_seconds: float = Field(default=30.0, ge=1, le=3_600)
    reflection_capacity_stale_after_seconds: int = Field(default=180, ge=5, le=3_600)
    reflection_capacity_sample_interval_seconds: float = Field(
        default=60.0,
        ge=5,
        le=3_600,
    )
    reflection_capacity_minimum_workers: int = Field(default=1, ge=1, le=100)
    reflection_capacity_maximum_workers: int = Field(default=32, ge=1, le=1_000)
    reflection_capacity_target_jobs_per_worker: int = Field(default=20, ge=1, le=10_000)
    reflection_capacity_ready_warning: int = Field(default=100, ge=1, le=1_000_000)
    reflection_capacity_ready_critical: int = Field(default=500, ge=1, le=1_000_000)
    reflection_capacity_oldest_warning_seconds: int = Field(
        default=300,
        ge=1,
        le=604_800,
    )
    reflection_capacity_oldest_critical_seconds: int = Field(
        default=1_800,
        ge=1,
        le=604_800,
    )
    reflection_capacity_dead_letter_warning: int = Field(
        default=1,
        ge=1,
        le=1_000_000,
    )
    reflection_capacity_dead_letter_critical: int = Field(
        default=10,
        ge=1,
        le=1_000_000,
    )
    reflection_capacity_policy_window_seconds: int = Field(
        default=3_600,
        ge=60,
        le=2_592_000,
    )
    reflection_capacity_policy_minimum_observations: int = Field(
        default=60,
        ge=2,
        le=100_000,
    )
    reflection_capacity_policy_cooldown_seconds: int = Field(
        default=3_600,
        ge=60,
        le=2_592_000,
    )
    reflection_capacity_governance_tenant_id: str = Field(
        default="default",
        min_length=1,
        max_length=100,
    )
    reflection_capacity_drift_window_seconds: int = Field(
        default=900,
        ge=60,
        le=2_592_000,
    )
    reflection_capacity_drift_minimum_observations: int = Field(
        default=3,
        ge=2,
        le=100_000,
    )
    reflection_capacity_drift_critical_observations: int = Field(
        default=10,
        ge=2,
        le=100_000,
    )
    reflection_capacity_drift_maximum_observations: int = Field(
        default=10_000,
        ge=2,
        le=100_000,
    )
    reflection_capacity_alert_response_warning_seconds: int = Field(
        default=900,
        ge=60,
        le=2_592_000,
    )
    reflection_capacity_alert_response_critical_seconds: int = Field(
        default=3_600,
        ge=60,
        le=2_592_000,
    )
    reflection_capacity_incident_audit_window_seconds: int = Field(
        default=300,
        ge=60,
        le=86_400,
    )
    reflection_capacity_incident_audit_warning_count: int = Field(
        default=5,
        ge=1,
        le=100_000,
    )
    reflection_capacity_incident_audit_critical_count: int = Field(
        default=10,
        ge=1,
        le=100_000,
    )
    reflection_capacity_incident_audit_maximum_events: int = Field(
        default=1_000,
        ge=1,
        le=100_000,
    )
    reflection_capacity_incident_reopen_warning_count: int = Field(
        default=2,
        ge=1,
        le=100_000,
    )
    reflection_capacity_incident_reopen_critical_count: int = Field(
        default=4,
        ge=1,
        le=100_000,
    )
    reflection_capacity_incident_maximum_alerts: int = Field(
        default=1_000,
        ge=1,
        le=100_000,
    )
    reflection_capacity_incident_maximum_incidents: int = Field(
        default=1_000,
        ge=1,
        le=100_000,
    )
    reflection_capacity_knowledge_quality_risk_window_seconds: int = Field(
        default=604_800,
        ge=3_600,
        le=2_592_000,
    )
    reflection_capacity_knowledge_unsafe_warning_count: int = Field(
        default=2,
        ge=2,
        le=100_000,
    )
    reflection_capacity_knowledge_unsafe_critical_count: int = Field(
        default=3,
        ge=2,
        le=100_000,
    )
    reflection_capacity_knowledge_degraded_warning_count: int = Field(
        default=2,
        ge=2,
        le=100_000,
    )
    reflection_capacity_knowledge_degraded_critical_count: int = Field(
        default=4,
        ge=2,
        le=100_000,
    )
    reflection_capacity_knowledge_quality_maximum_snapshots: int = Field(
        default=1_000,
        ge=2,
        le=100_000,
    )
    reflection_capacity_knowledge_quality_maximum_trend_buckets: int = Field(
        default=366,
        ge=1,
        le=3_660,
    )

    @field_validator(
        "reflection_handler_version",
        "reflection_worker_id",
        "reflection_capacity_governance_tenant_id",
    )
    @classmethod
    def normalize_reflection_worker_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("reflection worker text settings must not be blank")
        return normalized

    @model_validator(mode="after")
    def reject_development_secret_in_production(self) -> Settings:
        if (
            self.environment.lower() == "production"
            and self.secret_key.get_secret_value() == "development-only-secret"
        ):
            raise ValueError("PUBLIC_AGENT_SECRET_KEY must be set in production")
        if self.reflection_worker_retry_base_seconds > self.reflection_worker_retry_max_seconds:
            raise ValueError("reflection worker retry delays must be ordered")
        if self.reflection_worker_heartbeat_seconds >= self.reflection_worker_lease_seconds:
            raise ValueError("reflection worker heartbeat must be shorter than its lease")
        if (
            self.reflection_worker_poll_jitter_seconds
            > self.reflection_worker_poll_interval_seconds
        ):
            raise ValueError("reflection worker jitter must not exceed its poll interval")
        if self.reflection_capacity_minimum_workers > self.reflection_capacity_maximum_workers:
            raise ValueError("reflection capacity worker bounds must be ordered")
        if self.reflection_capacity_ready_warning > self.reflection_capacity_ready_critical:
            raise ValueError("reflection capacity ready thresholds must be ordered")
        if (
            self.reflection_capacity_oldest_warning_seconds
            > self.reflection_capacity_oldest_critical_seconds
        ):
            raise ValueError("reflection capacity age thresholds must be ordered")
        if (
            self.reflection_capacity_dead_letter_warning
            > self.reflection_capacity_dead_letter_critical
        ):
            raise ValueError("reflection capacity dead-letter thresholds must be ordered")
        if not (
            self.reflection_capacity_drift_minimum_observations
            <= self.reflection_capacity_drift_critical_observations
            <= self.reflection_capacity_drift_maximum_observations
        ):
            raise ValueError("reflection capacity drift sample thresholds must be ordered")
        if (
            self.reflection_capacity_alert_response_warning_seconds
            > self.reflection_capacity_alert_response_critical_seconds
        ):
            raise ValueError("reflection capacity alert response thresholds must be ordered")
        if (
            self.reflection_capacity_incident_audit_warning_count
            > self.reflection_capacity_incident_audit_critical_count
        ):
            raise ValueError("reflection capacity incident audit thresholds must be ordered")
        if (
            self.reflection_capacity_incident_audit_critical_count
            > self.reflection_capacity_incident_audit_maximum_events
        ):
            raise ValueError(
                "reflection capacity incident audit maximum must cover critical"
            )
        if (
            self.reflection_capacity_incident_reopen_warning_count
            > self.reflection_capacity_incident_reopen_critical_count
        ):
            raise ValueError("reflection capacity incident reopen thresholds must be ordered")
        if (
            self.reflection_capacity_knowledge_unsafe_warning_count
            > self.reflection_capacity_knowledge_unsafe_critical_count
        ):
            raise ValueError(
                "reflection capacity knowledge unsafe thresholds must be ordered"
            )
        if (
            self.reflection_capacity_knowledge_degraded_warning_count
            > self.reflection_capacity_knowledge_degraded_critical_count
        ):
            raise ValueError(
                "reflection capacity knowledge degraded thresholds must be ordered"
            )
        if max(
            self.reflection_capacity_knowledge_unsafe_critical_count,
            self.reflection_capacity_knowledge_degraded_critical_count,
        ) > self.reflection_capacity_knowledge_quality_maximum_snapshots:
            raise ValueError(
                "reflection capacity knowledge quality maximum snapshots must cover critical"
            )
        return self

    def require_management_api_secrets(self) -> None:
        if (
            self.environment.lower() == "production"
            and self.api_token_pepper.get_secret_value()
            == "development-only-token-pepper"
        ):
            raise ValueError("PUBLIC_AGENT_API_TOKEN_PEPPER must be set in production")
