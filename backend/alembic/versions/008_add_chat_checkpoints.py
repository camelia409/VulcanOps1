"""Add chat_checkpoints and chat_checkpoint_writes tables for LangGraph session memory.

Revision ID: 008_add_chat_checkpoints
Revises: 007_add_engineer_feedback
Create Date: 2026-06-20
"""

from alembic import op
import sqlalchemy as sa

revision = "008_add_chat_checkpoints"
down_revision = "007_add_engineer_feedback"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "chat_checkpoints",
        sa.Column("thread_id", sa.Text, nullable=False),
        sa.Column("checkpoint_ns", sa.Text, nullable=False, server_default=""),
        sa.Column("checkpoint_id", sa.Text, nullable=False),
        sa.Column("parent_checkpoint_id", sa.Text, nullable=True),
        sa.Column("type", sa.Text, nullable=False),
        sa.Column("data", sa.LargeBinary, nullable=False),
        sa.Column("meta_type", sa.Text, nullable=False),
        sa.Column("meta_data", sa.LargeBinary, nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.PrimaryKeyConstraint("thread_id", "checkpoint_ns", "checkpoint_id"),
    )
    op.create_index(
        "ix_chat_checkpoints_thread_ns",
        "chat_checkpoints",
        ["thread_id", "checkpoint_ns"],
    )

    op.create_table(
        "chat_checkpoint_writes",
        sa.Column("thread_id", sa.Text, nullable=False),
        sa.Column("checkpoint_ns", sa.Text, nullable=False, server_default=""),
        sa.Column("checkpoint_id", sa.Text, nullable=False),
        sa.Column("task_id", sa.Text, nullable=False),
        sa.Column("task_path", sa.Text, nullable=False, server_default=""),
        sa.Column("idx", sa.Integer, nullable=False),
        sa.Column("channel", sa.Text, nullable=False),
        sa.Column("type", sa.Text, nullable=False),
        sa.Column("data", sa.LargeBinary, nullable=False),
        sa.PrimaryKeyConstraint(
            "thread_id", "checkpoint_ns", "checkpoint_id", "task_id", "idx"
        ),
    )


def downgrade() -> None:
    op.drop_table("chat_checkpoint_writes")
    op.drop_index("ix_chat_checkpoints_thread_ns", table_name="chat_checkpoints")
    op.drop_table("chat_checkpoints")
