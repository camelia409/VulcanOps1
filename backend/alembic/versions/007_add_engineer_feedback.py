"""add engineer_feedback table

Revision ID: 007_add_engineer_feedback
Revises: 006_add_spare_parts
Create Date: 2026-06-20 00:00:00.000000+00:00

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "007_add_engineer_feedback"
down_revision: Union[str, None] = "006_add_spare_parts"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "engineer_feedback",
        sa.Column(
            "feedback_id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "report_batch_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "machine_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("failure_mode", sa.Text(), nullable=True),
        sa.Column("reported_root_cause", sa.Text(), nullable=True),
        sa.Column("thumbs", sa.Text(), nullable=True),
        sa.Column("verdict", sa.Text(), nullable=True),
        sa.Column("actual_root_cause", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("engineer_id", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "thumbs IN ('up', 'down') OR thumbs IS NULL",
            name="ck_engineer_feedback_thumbs",
        ),
        sa.CheckConstraint(
            "verdict IN ('correct', 'partial', 'wrong') OR verdict IS NULL",
            name="ck_engineer_feedback_verdict",
        ),
        sa.ForeignKeyConstraint(
            ["report_batch_id"],
            ["report_batches.batch_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["machine_id"],
            ["machines.machine_id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "report_batch_id", "engineer_id",
            name="uq_engineer_feedback_batch_engineer",
        ),
        sa.PrimaryKeyConstraint("feedback_id"),
    )
    op.create_index(
        "ix_engineer_feedback_machine_created",
        "engineer_feedback",
        ["machine_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_engineer_feedback_failure_mode",
        "engineer_feedback",
        ["failure_mode"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_engineer_feedback_failure_mode", table_name="engineer_feedback")
    op.drop_index("ix_engineer_feedback_machine_created", table_name="engineer_feedback")
    op.drop_table("engineer_feedback")
