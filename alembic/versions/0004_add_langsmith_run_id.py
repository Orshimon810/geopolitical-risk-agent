"""Add langsmith_run_id to analysis_history

Revision ID: 0002
Revises: 0001
Create Date: 2026-06-21

Adds a nullable UUID column that stores the root LangSmith trace ID for each
analysis run. Used by the feedback endpoint to target the correct trace when
calling langsmith.Client().create_feedback().
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "analysis_history",
        sa.Column(
            "langsmith_run_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.create_index(
        "idx_history_langsmith_run_id",
        "analysis_history",
        ["langsmith_run_id"],
    )


def downgrade() -> None:
    op.drop_index("idx_history_langsmith_run_id", table_name="analysis_history")
    op.drop_column("analysis_history", "langsmith_run_id")
