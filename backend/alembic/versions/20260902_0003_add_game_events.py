"""Add game_events table for live play-by-play ingestion."""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260902_0003"
down_revision: Union[str, Sequence[str], None] = "20260901_0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "game_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("game_pk", sa.Integer(), nullable=False),
        sa.Column("event_id", sa.String(length=64), nullable=False),
        sa.Column("type", sa.String(length=32), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column(
            "ingested_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("game_pk", "event_id", name="uq_game_events_game_pk_event_id"),
    )
    op.create_index("ix_game_events_game_pk", "game_events", ["game_pk"], unique=False)
    op.create_index(
        "ix_game_events_game_pk_sequence",
        "game_events",
        ["game_pk", "sequence"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_game_events_game_pk_sequence", table_name="game_events")
    op.drop_index("ix_game_events_game_pk", table_name="game_events")
    op.drop_table("game_events")
