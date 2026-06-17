"""Split value_usd into cost_basis_usd + last_price_usd on user_portfolios

Revision ID: 0003
Revises: 0002
Create Date: 2026-06-17

Rationale:
  value_usd was ambiguous — it conflated what the user paid (cost basis)
  with the current market value. This migration separates the two so that:
    - cost_basis_usd: what the user recorded as their entry value (immutable intent)
    - last_price_usd:  last known market price per unit (written by background refresh)
    - price_updated_at: when last_price_usd was last fetched

Apply:
  alembic upgrade head

Roll back:
  alembic downgrade -1
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE user_portfolios RENAME COLUMN value_usd TO cost_basis_usd"
    )
    op.execute(
        "ALTER TABLE user_portfolios ADD COLUMN IF NOT EXISTS last_price_usd NUMERIC(18, 2)"
    )
    op.execute(
        "ALTER TABLE user_portfolios ADD COLUMN IF NOT EXISTS price_updated_at TIMESTAMPTZ"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE user_portfolios DROP COLUMN IF EXISTS price_updated_at"
    )
    op.execute(
        "ALTER TABLE user_portfolios DROP COLUMN IF EXISTS last_price_usd"
    )
    op.execute(
        "ALTER TABLE user_portfolios RENAME COLUMN cost_basis_usd TO value_usd"
    )
