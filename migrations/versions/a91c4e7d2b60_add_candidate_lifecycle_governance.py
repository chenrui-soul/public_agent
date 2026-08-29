"""add candidate lifecycle governance

Revision ID: a91c4e7d2b60
Revises: d7b3a1e9f240
Create Date: 2026-08-25 06:00:00.000000

"""

from collections.abc import Sequence
from uuid import UUID

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a91c4e7d2b60"
down_revision: str | None = "d7b3a1e9f240"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_BACKFILL_BATCH_SIZE = 500
_UUID_PATTERN = (
    "^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-"
    "[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$"
)


def upgrade() -> None:
    op.add_column(
        "learning_candidates",
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "learning_candidates",
        sa.Column("protected_until", sa.DateTime(timezone=True), nullable=True),
    )
    op.drop_constraint(
        "ck_learning_candidates_status",
        "learning_candidates",
        type_="check",
    )
    op.create_check_constraint(
        "ck_learning_candidates_status",
        "learning_candidates",
        "status IN ("
        "'pending','evaluating','awaiting_approval','approved','active','deprecated',"
        "'expired','rolled_back','rejected'"
        ")",
    )
    op.create_index(
        "ix_learning_candidates_governance_scan",
        "learning_candidates",
        ["tenant_id", "agent_id", "domain_id", "status", "created_at", "id"],
        unique=False,
    )

    op.add_column(
        "memories",
        sa.Column("candidate_id", sa.UUID(), nullable=True),
    )
    op.add_column(
        "memories",
        sa.Column(
            "recall_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
    )
    op.add_column(
        "memories",
        sa.Column("last_recalled_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute(
        sa.text(
            """
            UPDATE memories AS memory
            SET candidate_id = (memory.metadata ->> 'candidate_id')::uuid
            WHERE memory.candidate_id IS NULL
              AND (memory.metadata ->> 'candidate_id') ~ :uuid_pattern
              AND EXISTS (
                  SELECT 1
                  FROM learning_candidates AS candidate
                  WHERE candidate.id = (memory.metadata ->> 'candidate_id')::uuid
              )
            """
        ).bindparams(uuid_pattern=_UUID_PATTERN)
    )
    op.create_foreign_key(
        "fk_memories_candidate_id_learning_candidates",
        "memories",
        "learning_candidates",
        ["candidate_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_memories_candidate_id",
        "memories",
        ["candidate_id"],
        unique=False,
    )
    op.create_unique_constraint(
        "uq_memories_candidate_id",
        "memories",
        ["candidate_id"],
    )
    op.create_check_constraint(
        "ck_memories_recall_count",
        "memories",
        "recall_count >= 0",
    )

    op.create_table(
        "candidate_lineages",
        sa.Column("child_candidate_id", sa.UUID(), nullable=False),
        sa.Column("source_candidate_id", sa.UUID(), nullable=False),
        sa.Column("relation_type", sa.String(length=32), nullable=False),
        sa.Column("source_version", sa.Integer(), nullable=False),
        sa.Column("source_status", sa.String(length=32), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "relation_type IN ('merge','compression')",
            name="ck_candidate_lineages_relation_type",
        ),
        sa.CheckConstraint(
            "source_version > 0",
            name="ck_candidate_lineages_source_version",
        ),
        sa.ForeignKeyConstraint(
            ["child_candidate_id"],
            ["learning_candidates.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_candidate_id"],
            ["learning_candidates.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("child_candidate_id", "source_candidate_id"),
    )
    _backfill_merge_lineages()
    op.create_index(
        "ix_candidate_lineages_source_child",
        "candidate_lineages",
        ["source_candidate_id", "child_candidate_id"],
        unique=False,
    )

    op.create_table(
        "candidate_governance_actions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("agent_id", sa.UUID(), nullable=False),
        sa.Column("candidate_id", sa.UUID(), nullable=False),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("reason_code", sa.String(length=100), nullable=False),
        sa.Column("policy_version", sa.String(length=100), nullable=False),
        sa.Column("idempotency_key", sa.String(length=500), nullable=False),
        sa.Column("value_score", sa.Float(), nullable=False),
        sa.Column("previous_status", sa.String(length=32), nullable=False),
        sa.Column("target_status", sa.String(length=32), nullable=False),
        sa.Column("replacement_candidate_id", sa.UUID(), nullable=True),
        sa.Column(
            "details",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "action IN ('expire','evict','compress')",
            name="ck_candidate_governance_actions_action",
        ),
        sa.CheckConstraint(
            "value_score >= 0 AND value_score <= 1",
            name="ck_candidate_governance_actions_value_score",
        ),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["candidate_id"],
            ["learning_candidates.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["replacement_candidate_id"],
            ["learning_candidates.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "idempotency_key",
            name="uq_candidate_governance_actions_idempotency",
        ),
    )
    op.create_index(
        "ix_candidate_governance_actions_scope_created",
        "candidate_governance_actions",
        ["tenant_id", "agent_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_candidate_governance_actions_candidate_created",
        "candidate_governance_actions",
        ["candidate_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_candidate_governance_actions_candidate_created",
        table_name="candidate_governance_actions",
    )
    op.drop_index(
        "ix_candidate_governance_actions_scope_created",
        table_name="candidate_governance_actions",
    )
    op.drop_table("candidate_governance_actions")

    op.drop_index(
        "ix_candidate_lineages_source_child",
        table_name="candidate_lineages",
    )
    op.drop_table("candidate_lineages")

    op.drop_constraint("ck_memories_recall_count", "memories", type_="check")
    op.execute(
        "ALTER TABLE memories DROP CONSTRAINT IF EXISTS uq_memories_candidate_id"
    )
    op.drop_index("ix_memories_candidate_id", table_name="memories")
    op.drop_constraint(
        "fk_memories_candidate_id_learning_candidates",
        "memories",
        type_="foreignkey",
    )
    op.drop_column("memories", "last_recalled_at")
    op.drop_column("memories", "recall_count")
    op.drop_column("memories", "candidate_id")

    op.drop_index(
        "ix_learning_candidates_governance_scan",
        table_name="learning_candidates",
    )
    op.drop_constraint(
        "ck_learning_candidates_status",
        "learning_candidates",
        type_="check",
    )
    op.create_check_constraint(
        "ck_learning_candidates_status",
        "learning_candidates",
        "status IN ("
        "'pending','evaluating','awaiting_approval','approved','active','deprecated',"
        "'rolled_back','rejected'"
        ")",
    )
    op.drop_column("learning_candidates", "protected_until")
    op.drop_column("learning_candidates", "expires_at")


def _backfill_merge_lineages() -> None:
    bind = op.get_bind()
    lineage = sa.table(
        "candidate_lineages",
        sa.column("child_candidate_id", sa.UUID()),
        sa.column("source_candidate_id", sa.UUID()),
        sa.column("relation_type", sa.String()),
        sa.column("source_version", sa.Integer()),
        sa.column("source_status", sa.String()),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )
    last_id: UUID | None = None
    while True:
        where = "proposed_change ? 'merge'"
        parameters: dict[str, object] = {"limit": _BACKFILL_BATCH_SIZE}
        if last_id is not None:
            where += " AND id > :last_id"
            parameters["last_id"] = last_id
        rows = bind.execute(
            sa.text(
                f"""
                SELECT id, proposed_change, created_at
                FROM learning_candidates
                WHERE {where}
                ORDER BY id
                LIMIT :limit
                """
            ),
            parameters,
        ).mappings().all()
        if not rows:
            return
        entries: list[dict[str, object]] = []
        for row in rows:
            merge = row["proposed_change"].get("merge")
            if not isinstance(merge, dict):
                raise ValueError(f"Candidate {row['id']} has invalid merge lineage")
            source_ids = merge.get("source_candidate_ids")
            versions = merge.get("source_versions")
            statuses = merge.get("source_statuses")
            if not isinstance(source_ids, list) or not isinstance(versions, dict):
                raise ValueError(f"Candidate {row['id']} has incomplete merge lineage")
            if not isinstance(statuses, dict):
                raise ValueError(f"Candidate {row['id']} has incomplete source statuses")
            for raw_source_id in source_ids:
                source_key = str(raw_source_id)
                if source_key not in versions or source_key not in statuses:
                    raise ValueError(f"Candidate {row['id']} has inconsistent merge lineage")
                entries.append(
                    {
                        "child_candidate_id": row["id"],
                        "source_candidate_id": UUID(source_key),
                        "relation_type": "merge",
                        "source_version": int(versions[source_key]),
                        "source_status": str(statuses[source_key]),
                        "created_at": row["created_at"],
                    }
                )
        if entries:
            bind.execute(
                postgresql.insert(lineage)
                .values(entries)
                .on_conflict_do_nothing(
                    index_elements=["child_candidate_id", "source_candidate_id"]
                )
            )
        last_id = rows[-1]["id"]
