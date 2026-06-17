"""add deep analysis jobs

Revision ID: 004_add_deep_analysis_jobs
Revises: 003_add_machine_dependent_tables
Create Date: 2026-06-16 18:00:00.000000+00:00

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "004_add_deep_analysis_jobs"
down_revision: Union[str, None] = "003_add_machine_dependent_tables"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "deep_analysis_jobs",
        sa.Column(
            "job_id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "machine_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "event_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column(
            "batch_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column(
            "status",
            sa.String(length=20),
            server_default="queued",
            nullable=False,
        ),
        sa.Column(
            "queued_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "completed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "duration_ms",
            sa.Integer(),
            nullable=True,
        ),
        sa.Column(
            "current_stage",
            sa.String(length=50),
            nullable=True,
        ),
        sa.Column(
            "progress_percent",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
        sa.Column(
            "error_message",
            sa.String(),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(
            ["machine_id"],
            ["machines.machine_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["event_id"],
            ["ingestion_events.event_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["batch_id"],
            ["report_batches.batch_id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("job_id"),
    )
    op.create_index(
        "ix_deep_analysis_jobs_machine_id",
        "deep_analysis_jobs",
        ["machine_id"],
        unique=False,
    )
    op.create_index(
        "ix_deep_analysis_jobs_status",
        "deep_analysis_jobs",
        ["status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_deep_analysis_jobs_status", table_name="deep_analysis_jobs"
    )
    op.drop_index(
        "ix_deep_analysis_jobs_machine_id", table_name="deep_analysis_jobs"
    )
    op.drop_table("deep_analysis_jobs")
