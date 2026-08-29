from public_agent.storage.models import Base


def test_core_production_tables_are_registered() -> None:
    expected = {
        "tenants",
        "agents",
        "api_principal_agent_grants",
        "api_principals",
        "api_tokens",
        "agent_versions",
        "domain_package_versions",
        "domain_package_assets",
        "domain_package_evaluations",
        "domain_package_approvals",
        "domain_package_releases",
        "runs",
        "run_events",
        "memories",
        "memory_embeddings",
        "knowledge_documents",
        "knowledge_ingestion_chunks",
        "knowledge_ingestion_jobs",
        "knowledge_chunks",
        "rag_evaluation_runs",
        "rag_evaluation_case_results",
        "learning_candidates",
        "candidate_lineages",
        "candidate_governance_actions",
        "evaluations",
        "approvals",
        "outbox_jobs",
        "outbox_job_archives",
        "reflection_capacity_observations",
        "reflection_capacity_calibrations",
        "reflection_capacity_policies",
        "reflection_capacity_change_requests",
        "reflection_capacity_governance_alerts",
        "reflection_capacity_governance_audit_events",
        "reflection_capacity_governance_incidents",
        "reflection_capacity_governance_remediations",
        "reflection_capacity_governance_postmortems",
        "reflection_capacity_governance_knowledge_feedback",
        "reflection_capacity_governance_knowledge_quality_snapshots",
        "reflection_capacity_governance_knowledge_recoveries",
        "reflection_job_operation_audit_events",
        "reflection_job_retry_requests",
        "reflection_worker_heartbeats",
    }

    assert expected.issubset(Base.metadata.tables)


def test_learning_candidate_fingerprint_has_an_independent_scoped_index() -> None:
    table = Base.metadata.tables["learning_candidates"]

    assert table.c.fingerprint.nullable is False
    assert any(
        index.name == "ix_learning_candidates_scope_fingerprint_status"
        and tuple(column.name for column in index.columns)
        == ("tenant_id", "agent_id", "domain_id", "fingerprint", "status")
        and index.unique is False
        for index in table.indexes
    )


def test_candidate_lifecycle_governance_has_first_class_columns_and_indexes() -> None:
    candidates = Base.metadata.tables["learning_candidates"]
    memories = Base.metadata.tables["memories"]
    lineages = Base.metadata.tables["candidate_lineages"]
    actions = Base.metadata.tables["candidate_governance_actions"]

    assert candidates.c.expires_at.nullable is True
    assert candidates.c.protected_until.nullable is True
    assert "ix_learning_candidates_governance_scan" in {
        index.name for index in candidates.indexes
    }
    assert memories.c.candidate_id.nullable is True
    assert memories.c.recall_count.nullable is False
    assert memories.c.last_recalled_at.nullable is True
    assert "ix_memories_candidate_id" in {index.name for index in memories.indexes}
    assert "uq_memories_candidate_id" in {
        constraint.name for constraint in memories.constraints
    }
    assert tuple(column.name for column in lineages.primary_key.columns) == (
        "child_candidate_id",
        "source_candidate_id",
    )
    assert "uq_candidate_governance_actions_idempotency" in {
        constraint.name for constraint in actions.constraints
    }


def test_knowledge_chunks_have_fulltext_and_vector_indexes() -> None:
    table = Base.metadata.tables["knowledge_chunks"]
    indexes = {index.name: index for index in table.indexes}

    assert table.c.embedding.type.dim == 384
    assert table.c.lexical_text.nullable is False
    assert table.c.lexical_profile.nullable is False
    assert indexes["ix_knowledge_chunks_search_vector_gin"].dialect_options["postgresql"][
        "using"
    ] == "gin"
    assert tuple(
        column.name for column in indexes["ix_knowledge_chunks_lexical_profile"].columns
    ) == ("tenant_id", "agent_id", "lexical_profile")
    assert indexes["ix_knowledge_chunks_embedding_hnsw"].dialect_options["postgresql"][
        "using"
    ] == "hnsw"


def test_knowledge_ingestion_tables_have_lease_progress_and_staging_constraints() -> None:
    jobs = Base.metadata.tables["knowledge_ingestion_jobs"]
    chunks = Base.metadata.tables["knowledge_ingestion_chunks"]
    job_constraints = {constraint.name for constraint in jobs.constraints}
    chunk_constraints = {constraint.name for constraint in chunks.constraints}

    assert {
        "ck_knowledge_ingestion_jobs_hashes",
        "ck_knowledge_ingestion_jobs_lease",
        "ck_knowledge_ingestion_jobs_parsed_text_size",
        "ck_knowledge_ingestion_jobs_progress",
        "ck_knowledge_ingestion_jobs_source_size",
        "ck_knowledge_ingestion_jobs_terminal",
        "uq_knowledge_ingestion_jobs_scope_idempotency",
    }.issubset(job_constraints)
    assert {
        "ck_knowledge_ingestion_chunks_char_range",
        "ck_knowledge_ingestion_chunks_content",
        "ck_knowledge_ingestion_chunks_index",
        "uq_knowledge_ingestion_chunks_job_index",
    }.issubset(chunk_constraints)
    assert chunks.c.embedding.nullable is True
    assert chunks.c.embedding.type.dim == 384
    assert "ix_knowledge_ingestion_jobs_scope_status_created" in {
        index.name for index in jobs.indexes
    }
    assert "ix_knowledge_ingestion_jobs_status_lease" in {
        index.name for index in jobs.indexes
    }
    assert "ix_knowledge_ingestion_chunks_job" in {
        index.name for index in chunks.indexes
    }


def test_rag_evaluation_tables_have_run_and_case_indexes() -> None:
    runs = Base.metadata.tables["rag_evaluation_runs"]
    cases = Base.metadata.tables["rag_evaluation_case_results"]

    assert "ix_rag_evaluation_runs_scope_dataset_created" in {
        index.name for index in runs.indexes
    }
    assert "ix_rag_evaluation_case_results_run_passed" in {
        index.name for index in cases.indexes
    }


def test_runs_and_approvals_have_resume_lease_indexes() -> None:
    runs = Base.metadata.tables["runs"]
    approvals = Base.metadata.tables["approvals"]

    assert runs.c.resume_token.nullable is True
    assert runs.c.resume_lease_expires_at.nullable is True
    assert "ix_runs_status_resume_lease" in {index.name for index in runs.indexes}
    assert "ix_approvals_run_created" in {index.name for index in approvals.indexes}
    assert "ck_runs_resume_lease_consistent" in {
        constraint.name for constraint in runs.constraints
    }


def test_reflection_job_operations_have_versions_scoped_idempotency_and_audit() -> None:
    jobs = Base.metadata.tables["outbox_jobs"]
    requests = Base.metadata.tables["reflection_job_retry_requests"]
    audit = Base.metadata.tables["reflection_job_operation_audit_events"]

    assert jobs.c.version.nullable is False
    assert jobs.c.attempts_in_cycle.nullable is False
    assert {
        "ck_outbox_jobs_attempts_in_cycle",
        "ck_outbox_jobs_version",
        "uq_outbox_jobs_id_tenant",
    }.issubset({constraint.name for constraint in jobs.constraints})
    assert {
        "fk_reflection_job_retry_requests_agent_scope",
        "fk_reflection_job_retry_requests_job_scope",
        "fk_reflection_job_retry_requests_run_scope",
        "uq_reflection_job_retry_requests_idempotency",
    }.issubset({constraint.name for constraint in requests.constraints})
    assert requests.c.idempotency_key_hash.nullable is False
    assert "ix_reflection_job_operation_audit_tenant_created" in {
        index.name for index in audit.indexes
    }


def test_reflection_capacity_queries_have_handler_scoped_indexes() -> None:
    jobs = Base.metadata.tables["outbox_jobs"]
    workers = Base.metadata.tables["reflection_worker_heartbeats"]
    job_indexes = {index.name: index for index in jobs.indexes}
    worker_indexes = {index.name: index for index in workers.indexes}

    assert tuple(
        column.name
        for column in job_indexes[
            "ix_outbox_jobs_handler_status_available"
        ].columns
    ) == ("job_type", "handler_version", "status", "available_at")
    assert tuple(
        column.name
        for column in worker_indexes[
            "ix_reflection_worker_heartbeats_handler_seen"
        ].columns
    ) == ("job_type", "handler_version", "last_seen_at", "status")


def test_capacity_history_and_partitioned_archive_models_are_registered() -> None:
    jobs = Base.metadata.tables["outbox_jobs"]
    archives = Base.metadata.tables["outbox_job_archives"]
    observations = Base.metadata.tables["reflection_capacity_observations"]
    calibrations = Base.metadata.tables["reflection_capacity_calibrations"]

    assert jobs.c.last_started_at.nullable is True
    assert jobs.c.last_processing_duration_ms.nullable is True
    assert jobs.c.total_processing_duration_ms.nullable is False
    assert "ix_outbox_jobs_handler_completed_duration" in {
        index.name for index in jobs.indexes
    }
    assert archives.dialect_options["postgresql"]["partition_by"] == (
        "RANGE (completed_at)"
    )
    assert tuple(column.name for column in archives.primary_key.columns) == (
        "id",
        "completed_at",
        "version",
    )
    assert "uq_reflection_capacity_observations_sample" in {
        constraint.name for constraint in observations.constraints
    }
    assert "ix_reflection_capacity_calibrations_handler_created" in {
        index.name for index in calibrations.indexes
    }


def test_capacity_policy_governance_models_are_versioned_and_handler_scoped() -> None:
    policies = Base.metadata.tables["reflection_capacity_policies"]
    requests = Base.metadata.tables["reflection_capacity_change_requests"]
    policy_indexes = {index.name: index for index in policies.indexes}

    assert "uq_reflection_capacity_policies_version" in {
        constraint.name for constraint in policies.constraints
    }
    assert policy_indexes["uq_reflection_capacity_policies_active"].unique is True
    assert policy_indexes["uq_reflection_capacity_policies_active"].dialect_options[
        "postgresql"
    ]["where"] is not None
    assert requests.c.version.nullable is False
    assert requests.c.base_policy_id.nullable is False
    assert requests.c.published_policy_id.nullable is True
    assert requests.c.rejected_by.nullable is True
    assert requests.c.rejected_at.nullable is True
    assert "uq_reflection_capacity_change_requests_calibration" in {
        constraint.name for constraint in requests.constraints
    }


def test_capacity_control_plane_has_deduplicated_alerts_and_append_only_audit() -> None:
    alerts = Base.metadata.tables["reflection_capacity_governance_alerts"]
    audit = Base.metadata.tables["reflection_capacity_governance_audit_events"]

    assert alerts.c.version.nullable is False
    assert alerts.c.last_observation_at.nullable is False
    assert "uq_reflection_capacity_governance_alerts_dedupe" in {
        constraint.name for constraint in alerts.constraints
    }
    assert "ix_reflection_capacity_governance_alerts_handler_status" in {
        index.name for index in alerts.indexes
    }
    assert "ix_reflection_capacity_governance_audit_tenant_created" in {
        index.name for index in audit.indexes
    }
    assert "ix_reflection_capacity_governance_audit_filter_created" in {
        index.name for index in audit.indexes
    }


def test_capacity_incidents_have_versioned_lifecycle_and_bounded_indexes() -> None:
    incidents = Base.metadata.tables["reflection_capacity_governance_incidents"]
    remediations = Base.metadata.tables[
        "reflection_capacity_governance_remediations"
    ]
    audit = Base.metadata.tables["reflection_capacity_governance_audit_events"]

    assert incidents.c.version.nullable is False
    assert incidents.c.last_evidence_at.nullable is False
    assert incidents.c.evidence.nullable is False
    assert "uq_reflection_capacity_governance_incidents_fingerprint" in {
        constraint.name for constraint in incidents.constraints
    }
    assert "ix_reflection_capacity_governance_incidents_tenant_status" in {
        index.name for index in incidents.indexes
    }
    assert "ix_reflection_capacity_governance_incidents_source" in {
        index.name for index in incidents.indexes
    }
    incident_signal_constraint = next(
        constraint
        for constraint in incidents.constraints
        if constraint.name == "ck_reflection_capacity_governance_incidents_signal"
    )
    remediation_playbook_constraint = next(
        constraint
        for constraint in remediations.constraints
        if constraint.name == "ck_capacity_remediations_playbook"
    )
    assert "knowledge_unsafe_persistent" in str(incident_signal_constraint.sqltext)
    assert "knowledge_degraded_repeat" in str(incident_signal_constraint.sqltext)
    assert "knowledge_requarantined" in str(incident_signal_constraint.sqltext)
    assert "knowledge_safety_containment" in str(
        remediation_playbook_constraint.sqltext
    )
    assert audit.c.incident_id.nullable is True


def test_governance_knowledge_quality_and_recovery_models_preserve_lineage() -> None:
    postmortems = Base.metadata.tables["reflection_capacity_governance_postmortems"]
    feedback = Base.metadata.tables[
        "reflection_capacity_governance_knowledge_feedback"
    ]
    snapshots = Base.metadata.tables[
        "reflection_capacity_governance_knowledge_quality_snapshots"
    ]
    recoveries = Base.metadata.tables[
        "reflection_capacity_governance_knowledge_recoveries"
    ]
    snapshot_constraints = {constraint.name for constraint in snapshots.constraints}
    recovery_constraints = {constraint.name for constraint in recoveries.constraints}
    recovery_indexes = {index.name: index for index in recoveries.indexes}

    assert postmortems.c.last_quarantined_at.nullable is True
    assert postmortems.c.quarantine_feedback_id.nullable is True
    assert postmortems.c.restore_count.nullable is False
    assert postmortems.c.last_restored_at.nullable is True
    assert {
        "ck_capacity_postmortems_quarantine_history",
        "ck_capacity_postmortems_restore_history",
    }.issubset({constraint.name for constraint in postmortems.constraints})
    assert "uq_capacity_knowledge_feedback_reporter_version" in {
        constraint.name for constraint in feedback.constraints
    }
    assert {
        "ck_capacity_knowledge_quality_assessment",
        "ck_capacity_knowledge_quality_counts",
        "ck_capacity_knowledge_quality_versions",
        "uq_capacity_knowledge_quality_evidence",
    }.issubset(snapshot_constraints)
    assert "updated_at" not in snapshots.c
    assert "ix_capacity_knowledge_quality_tenant_assessment" in {
        index.name for index in snapshots.indexes
    }
    assert "ix_capacity_knowledge_quality_tenant_captured" in {
        index.name for index in snapshots.indexes
    }
    assert {
        "ck_capacity_knowledge_recoveries_lifecycle",
        "ck_capacity_knowledge_recoveries_reason",
        "ck_capacity_knowledge_recoveries_status",
        "ck_capacity_knowledge_recoveries_versions",
    }.issubset(recovery_constraints)
    assert recoveries.c.snapshot_id.nullable is False
    assert recoveries.c.quarantine_feedback_id.nullable is False
    assert recovery_indexes["uq_capacity_knowledge_recoveries_active"].unique is True
    assert recovery_indexes[
        "uq_capacity_knowledge_recoveries_active"
    ].dialect_options["postgresql"]["where"] is not None
    assert "ix_capacity_knowledge_recoveries_tenant_status" in recovery_indexes


def test_api_authentication_tables_scope_tokens_and_agent_grants() -> None:
    principals = Base.metadata.tables["api_principals"]
    grants = Base.metadata.tables["api_principal_agent_grants"]
    tokens = Base.metadata.tables["api_tokens"]

    assert "uq_api_principals_tenant_subject" in {
        constraint.name for constraint in principals.constraints
    }
    assert "uq_api_principals_id_tenant" in {
        constraint.name for constraint in principals.constraints
    }
    assert tuple(column.name for column in grants.primary_key.columns) == (
        "principal_id",
        "agent_id",
    )
    assert "fk_api_principal_agent_grants_agent_scope" in {
        constraint.name for constraint in grants.constraints
    }
    assert "fk_api_principal_agent_grants_principal_scope" in {
        constraint.name for constraint in grants.constraints
    }
    assert tokens.c.secret_digest.nullable is False
    assert "uq_api_tokens_prefix" in {
        constraint.name for constraint in tokens.constraints
    }
    assert "ck_api_tokens_digest_size" in {
        constraint.name for constraint in tokens.constraints
    }
    assert "ix_api_tokens_principal_active" in {
        index.name for index in tokens.indexes
    }


def test_domain_package_release_tables_have_immutability_and_audit_constraints() -> None:
    versions = Base.metadata.tables["domain_package_versions"]
    assets = Base.metadata.tables["domain_package_assets"]
    evaluations = Base.metadata.tables["domain_package_evaluations"]
    approvals = Base.metadata.tables["domain_package_approvals"]
    releases = Base.metadata.tables["domain_package_releases"]

    assert "uq_domain_package_versions_scope_version" in {
        constraint.name for constraint in versions.constraints
    }
    assert "uq_domain_package_versions_agent_version" in {
        constraint.name for constraint in versions.constraints
    }
    assert "ix_domain_package_versions_scope_status" in {
        index.name for index in versions.indexes
    }
    assert "uq_domain_package_assets_version_type_key" in {
        constraint.name for constraint in assets.constraints
    }
    assert "uq_domain_package_assets_version_path" in {
        constraint.name for constraint in assets.constraints
    }
    assert "uq_domain_package_evaluations_version_report" in {
        constraint.name for constraint in evaluations.constraints
    }
    assert "uq_domain_package_approvals_version" in {
        constraint.name for constraint in approvals.constraints
    }
    assert "uq_domain_package_releases_scope_idempotency" in {
        constraint.name for constraint in releases.constraints
    }
