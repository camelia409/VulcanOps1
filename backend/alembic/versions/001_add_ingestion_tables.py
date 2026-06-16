"""add_ingestion_tables

Revision ID: 001_add_ingestion_tables
Revises: 
Create Date: 2026-06-15 20:52:15.640000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "001_add_ingestion_tables"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ingestion_events",
        sa.Column(
            "event_id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "triggered_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("triggered_by", sa.String(length=50), nullable=False),
        sa.Column(
            "files_uploaded",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.String(length=20),
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.Column(
            "machines_found",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("event_id"),
    )

    op.create_table(
        "report_batches",
        sa.Column(
            "batch_id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "event_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column(
            "machine_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column(
            "generated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("root_cause", sa.String(), nullable=True),
        sa.Column("failure_mode", sa.String(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("risk_level", sa.String(length=50), nullable=True),
        sa.Column("recommended_action", sa.String(), nullable=True),
        sa.Column("priority", sa.String(length=50), nullable=True),
        sa.Column("rul_hours", sa.Float(), nullable=True),
        sa.Column("verification_passed", sa.Boolean(), nullable=True),
        sa.Column(
            "pipeline_errors",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "full_report_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["event_id"],
            ["ingestion_events.event_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["machine_id"],
            ["machines.machine_id"],
        ),
        sa.UniqueConstraint(
            "event_id", "machine_id", name="uq_report_batch_event_machine"
        ),
        sa.PrimaryKeyConstraint("batch_id"),
    )

    op.create_table(
        "stored_role_reports",
        sa.Column(
            "report_id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "batch_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "generated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["batch_id"],
            ["report_batches.batch_id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "batch_id", "role", name="uq_stored_role_report_batch_role"
        ),
        sa.PrimaryKeyConstraint("report_id"),
    )


def downgrade() -> None:
    op.drop_table("stored_role_reports")
    op.drop_table("report_batches")
    op.drop_table("ingestion_events")
