import pytest
from pydantic import ValidationError

from public_agent.config import Settings


def test_production_rejects_default_secret() -> None:
    with pytest.raises(ValidationError, match="SECRET_KEY must be set"):
        Settings(environment="production")


def test_production_rejects_default_api_token_pepper() -> None:
    settings = Settings(
        _env_file=None,
        environment="production",
        secret_key="production-secret",
    )

    with pytest.raises(ValueError, match="API_TOKEN_PEPPER must be set"):
        settings.require_management_api_secrets()


def test_openai_generation_settings_have_bounded_production_defaults() -> None:
    settings = Settings(_env_file=None)

    assert settings.openai_model == "gpt-5.6-terra"
    assert settings.openai_max_output_tokens == 4096
    assert settings.openai_timeout_seconds == 60
    assert settings.openai_max_retries == 2
    assert settings.openai_retry_backoff_seconds == 0.25


def test_openai_generation_settings_reject_invalid_retry_count() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, openai_max_retries=6)


def test_reflection_capacity_settings_load_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PUBLIC_AGENT_REFLECTION_CAPACITY_MINIMUM_WORKERS", "2")
    monkeypatch.setenv("PUBLIC_AGENT_REFLECTION_CAPACITY_MAXIMUM_WORKERS", "40")
    monkeypatch.setenv("PUBLIC_AGENT_REFLECTION_CAPACITY_READY_WARNING", "200")
    monkeypatch.setenv("PUBLIC_AGENT_REFLECTION_CAPACITY_READY_CRITICAL", "800")
    monkeypatch.setenv(
        "PUBLIC_AGENT_REFLECTION_CAPACITY_SAMPLE_INTERVAL_SECONDS",
        "30",
    )

    settings = Settings(_env_file=None)

    assert settings.reflection_capacity_minimum_workers == 2
    assert settings.reflection_capacity_maximum_workers == 40
    assert settings.reflection_capacity_ready_warning == 200
    assert settings.reflection_capacity_ready_critical == 800
    assert settings.reflection_capacity_sample_interval_seconds == 30


def test_reflection_capacity_sampling_interval_is_bounded() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, reflection_capacity_sample_interval_seconds=4)


def test_reflection_capacity_policy_governance_defaults_are_bounded() -> None:
    settings = Settings(_env_file=None)

    assert settings.reflection_capacity_policy_window_seconds == 3_600
    assert settings.reflection_capacity_policy_minimum_observations == 60
    assert settings.reflection_capacity_policy_cooldown_seconds == 3_600
    with pytest.raises(ValidationError):
        Settings(_env_file=None, reflection_capacity_policy_window_seconds=59)


def test_reflection_capacity_drift_settings_are_bounded_and_ordered() -> None:
    settings = Settings(_env_file=None)

    assert settings.reflection_capacity_drift_window_seconds == 900
    assert settings.reflection_capacity_drift_minimum_observations == 3
    assert settings.reflection_capacity_drift_critical_observations == 10
    assert settings.reflection_capacity_alert_response_warning_seconds == 900
    assert settings.reflection_capacity_alert_response_critical_seconds == 3_600
    with pytest.raises(ValidationError, match="drift sample thresholds"):
        Settings(
            _env_file=None,
            reflection_capacity_drift_minimum_observations=20,
            reflection_capacity_drift_critical_observations=10,
        )
    with pytest.raises(ValidationError, match="alert response thresholds"):
        Settings(
            _env_file=None,
            reflection_capacity_alert_response_warning_seconds=3_600,
            reflection_capacity_alert_response_critical_seconds=900,
        )


def test_reflection_capacity_incident_settings_are_bounded_and_ordered() -> None:
    settings = Settings(_env_file=None)

    assert settings.reflection_capacity_incident_audit_window_seconds == 300
    assert settings.reflection_capacity_incident_audit_warning_count == 5
    assert settings.reflection_capacity_incident_audit_critical_count == 10
    assert settings.reflection_capacity_incident_reopen_warning_count == 2
    assert settings.reflection_capacity_incident_reopen_critical_count == 4
    with pytest.raises(ValidationError, match="incident audit thresholds"):
        Settings(
            _env_file=None,
            reflection_capacity_incident_audit_warning_count=11,
            reflection_capacity_incident_audit_critical_count=10,
        )
    with pytest.raises(ValidationError, match="incident reopen thresholds"):
        Settings(
            _env_file=None,
            reflection_capacity_incident_reopen_warning_count=5,
            reflection_capacity_incident_reopen_critical_count=4,
        )


def test_knowledge_quality_risk_settings_are_bounded_and_ordered() -> None:
    settings = Settings(_env_file=None)

    assert settings.reflection_capacity_knowledge_quality_risk_window_seconds == 604_800
    assert settings.reflection_capacity_knowledge_unsafe_warning_count == 2
    assert settings.reflection_capacity_knowledge_unsafe_critical_count == 3
    assert settings.reflection_capacity_knowledge_degraded_warning_count == 2
    assert settings.reflection_capacity_knowledge_degraded_critical_count == 4
    assert settings.reflection_capacity_knowledge_quality_maximum_snapshots == 1_000
    assert settings.reflection_capacity_knowledge_quality_maximum_trend_buckets == 366
    with pytest.raises(ValidationError, match="knowledge unsafe thresholds"):
        Settings(
            _env_file=None,
            reflection_capacity_knowledge_unsafe_warning_count=4,
            reflection_capacity_knowledge_unsafe_critical_count=3,
        )
    with pytest.raises(ValidationError, match="knowledge degraded thresholds"):
        Settings(
            _env_file=None,
            reflection_capacity_knowledge_degraded_warning_count=5,
            reflection_capacity_knowledge_degraded_critical_count=4,
        )
    with pytest.raises(ValidationError, match="maximum snapshots"):
        Settings(
            _env_file=None,
            reflection_capacity_knowledge_unsafe_critical_count=10,
            reflection_capacity_knowledge_quality_maximum_snapshots=9,
        )


@pytest.mark.parametrize(
    "overrides",
    [
        {
            "reflection_capacity_minimum_workers": 3,
            "reflection_capacity_maximum_workers": 2,
        },
        {
            "reflection_capacity_ready_warning": 501,
            "reflection_capacity_ready_critical": 500,
        },
        {
            "reflection_capacity_oldest_warning_seconds": 1_801,
            "reflection_capacity_oldest_critical_seconds": 1_800,
        },
        {
            "reflection_capacity_dead_letter_warning": 11,
            "reflection_capacity_dead_letter_critical": 10,
        },
    ],
)
def test_reflection_capacity_settings_reject_unordered_thresholds(
    overrides: dict[str, int],
) -> None:
    with pytest.raises(ValidationError, match="reflection capacity"):
        Settings(_env_file=None, **overrides)
