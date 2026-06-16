"""add machine dependent tables

Revision ID: 003_add_machine_dependent_tables
Revises: 51028d1a85d1
Create Date: 2026-06-16 06:00:00.000000+00:00

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "003_add_machine_dependent_tables"
down_revision: Union[str, None] = "51028d1a85d1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "maintenance_records",
        sa.Column(
            "maintenance_id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "machine_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("failure_mode", sa.String(length=255), nullable=False),
        sa.Column("action_taken", sa.Text(), nullable=False),
        sa.Column("downtime_hours", sa.Float(), nullable=False),
        sa.Column("engineer", sa.String(length=255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["machine_id"],
            ["machines.machine_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("maintenance_id"),
    )
    op.create_index(
        "ix_maintenance_records_machine_id",
        "maintenance_records",
        ["machine_id"],
        unique=False,
    )

    op.create_table(
        "sensor_readings",
        sa.Column(
            "id",
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
            "timestamp",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column("temperature", sa.Float(), nullable=True),
        sa.Column("vibration", sa.Float(), nullable=True),
        sa.Column("pressure", sa.Float(), nullable=True),
        sa.Column("load", sa.Float(), nullable=True),
        sa.Column("rpm", sa.Float(), nullable=True),
        sa.Column(
            "ingested_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["machine_id"],
            ["machines.machine_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_sensor_readings_machine_id",
        "sensor_readings",
        ["machine_id"],
        unique=False,
    )
    op.create_index(
        "ix_sensor_readings_timestamp",
        "sensor_readings",
        ["timestamp"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_sensor_readings_timestamp", table_name="sensor_readings")
    op.drop_index("ix_sensor_readings_machine_id", table_name="sensor_readings")
    op.drop_table("sensor_readings")
    op.drop_index(
        "ix_maintenance_records_machine_id", table_name="maintenance_records"
    )
    op.drop_table("maintenance_records")
