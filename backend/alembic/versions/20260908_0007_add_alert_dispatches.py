"""Add alert_dispatches idempotency table for Stage 6.3 rules."""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260908_0007"
down_revision: Union[str, Sequence[str], None] = "20260907_0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "alert_dispatches",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("alert_type", sa.String(length=32), nullable=False),
        sa.Column("dedupe_key", sa.String(length=255), nullable=False),
        sa.Column("game_pk", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            "alert_type",
            "dedupe_key",
            name="uq_alert_dispatches_user_type_key",
        ),
    )
    op.create_index(
        "ix_alert_dispatches_game_pk",
        "alert_dispatches",
        ["game_pk"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_alert_dispatches_game_pk", table_name="alert_dispatches")
    op.drop_table("alert_dispatches")
