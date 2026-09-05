"""Persist last live scoreboard snapshot on games for degraded reads."""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260905_0004"
down_revision: Union[str, Sequence[str], None] = "20260902_0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("games", sa.Column("live_state", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("games", "live_state")
