"""Add document_chunks table with optional pgvector embeddings.

Primary store for all ingested manuals and SOPs.  Replaces disk-based
/storage/uploads/<type>/<stem>.txt files which are lost on Render redeploys.

pgvector availability:
  - Neon (production): pgvector is pre-installed → vector(384) column + ivfflat index.
  - Local PostgreSQL without pgvector: table is created without the embedding
    column; retrieval falls back to keyword search automatically.

The code layer (embedding_service + evidence_retrieval_agent) handles both
cases gracefully via the "embedding IS NULL → keyword fallback" branch.

Revision ID: 005_add_document_chunks_pgvector
Revises: 004_add_deep_analysis_jobs
Create Date: 2026-06-17
"""

from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "005_add_document_chunks_pgvector"
down_revision: Union[str, None] = "004_add_deep_analysis_jobs"
branch_labels = None
depends_on = None

_VECTOR_DIM = 384  # BAAI/bge-small-en-v1.5 output dimension


def _pgvector_available(conn) -> bool:
    """Check whether the pgvector extension can be installed on this server."""
    try:
        conn.execute(sa.text("SAVEPOINT _pgvector_probe"))
        conn.execute(sa.text("CREATE EXTENSION IF NOT EXISTS vector"))
        conn.execute(sa.text("RELEASE SAVEPOINT _pgvector_probe"))
        return True
    except Exception:
        conn.execute(sa.text("ROLLBACK TO SAVEPOINT _pgvector_probe"))
        return False


def upgrade() -> None:
    conn = op.get_bind()
    has_pgvector = _pgvector_available(conn)

    op.create_table(
        "document_chunks",
        sa.Column("chunk_id", sa.UUID(), nullable=False),
        sa.Column("source_filename", sa.String(length=512), nullable=False),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("chunk_text", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("chunk_id"),
    )

    if has_pgvector:
        conn.execute(sa.text(
            f"ALTER TABLE document_chunks ADD COLUMN embedding vector({_VECTOR_DIM})"
        ))
        # ivfflat index for approximate nearest-neighbour cosine search.
        # lists=50 is appropriate for a corpus up to ~50 000 chunks.
        conn.execute(sa.text(
            "CREATE INDEX ix_document_chunks_embedding "
            "ON document_chunks USING ivfflat (embedding vector_cosine_ops) "
            "WITH (lists = 50)"
        ))
    else:
        # No pgvector on this server — store embeddings as JSON text.
        # The retrieval agent detects null/absent vector column and falls
        # back to keyword search automatically.
        conn.execute(sa.text(
            "ALTER TABLE document_chunks ADD COLUMN embedding TEXT"
        ))

    op.create_index("ix_document_chunks_source_filename", "document_chunks", ["source_filename"])
    op.create_index("ix_document_chunks_source_type", "document_chunks", ["source_type"])


def downgrade() -> None:
    op.drop_index("ix_document_chunks_source_type", table_name="document_chunks")
    op.drop_index("ix_document_chunks_source_filename", table_name="document_chunks")
    op.execute("DROP INDEX IF EXISTS ix_document_chunks_embedding")
    op.drop_table("document_chunks")
