"""Add insufficient_data to confidence enum in analysis_history

Revision ID: 0005
Revises: 0004
Create Date: 2026-07-01

Widens the confidence column from VARCHAR(10) to VARCHAR(20) and updates
the CHECK constraint to allow the new 'insufficient_data' sentinel value.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Drop the old constraint before altering the column or adding the new one.
    op.drop_constraint("chk_history_confidence", "analysis_history", type_="check")

    # Widen the column to accommodate 'insufficient_data' (17 chars).
    op.alter_column(
        "analysis_history",
        "confidence",
        existing_type=sa.String(10),
        type_=sa.String(20),
        existing_nullable=False,
    )

    # Recreate the constraint including the new value.
    op.create_check_constraint(
        "chk_history_confidence",
        "analysis_history",
        "confidence IN ('Low', 'Medium', 'High', 'insufficient_data')",
    )


def downgrade() -> None:
    op.drop_constraint("chk_history_confidence", "analysis_history", type_="check")

    # Rows with 'insufficient_data' must be remapped before narrowing the column.
    op.execute(
        "UPDATE analysis_history SET confidence = 'Low' WHERE confidence = 'insufficient_data'"
    )

    op.alter_column(
        "analysis_history",
        "confidence",
        existing_type=sa.String(20),
        type_=sa.String(10),
        existing_nullable=False,
    )

    op.create_check_constraint(
        "chk_history_confidence",
        "analysis_history",
        "confidence IN ('Low', 'Medium', 'High')",
    )
