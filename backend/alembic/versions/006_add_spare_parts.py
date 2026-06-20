"""add spare_parts table

Revision ID: 006_add_spare_parts
Revises: 005_add_document_chunks_pgvector
Create Date: 2026-06-19 12:00:00.000000+00:00

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "006_add_spare_parts"
down_revision: Union[str, None] = "005_add_document_chunks_pgvector"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "spare_parts",
        sa.Column(
            "part_id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("part_name", sa.Text(), nullable=False),
        sa.Column("category", sa.Text(), nullable=True),
        sa.Column("qty_on_hand", sa.Integer(), server_default="0", nullable=False),
        sa.Column("reorder_threshold", sa.Integer(), server_default="0", nullable=False),
        sa.Column("lead_time_days", sa.Integer(), nullable=False),
        sa.Column("unit_cost_usd", sa.Numeric(10, 2), nullable=True),
        sa.Column("supplier", sa.Text(), nullable=True),
        sa.Column(
            "last_updated",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("part_id"),
        sa.UniqueConstraint("part_name", "supplier", name="uq_spare_parts_name_supplier"),
    )
    op.create_index(
        "ix_spare_parts_category",
        "spare_parts",
        ["category"],
        unique=False,
    )
    op.create_index(
        "ix_spare_parts_part_name",
        "spare_parts",
        ["part_name"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_spare_parts_part_name", table_name="spare_parts")
    op.drop_index("ix_spare_parts_category", table_name="spare_parts")
    op.drop_table("spare_parts")
