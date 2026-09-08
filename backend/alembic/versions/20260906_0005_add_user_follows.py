"""Add user_follows watchlist table for teams and players."""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260906_0005"
down_revision: Union[str, Sequence[str], None] = "20260905_0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "user_follows",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("entity_type", sa.String(length=16), nullable=False),
        sa.Column("team_id", sa.Integer(), nullable=True),
        sa.Column("player_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(entity_type = 'team' AND team_id IS NOT NULL AND player_id IS NULL) "
            "OR (entity_type = 'player' AND player_id IS NOT NULL AND team_id IS NULL)",
            name="ck_user_follows_entity_target",
        ),
        sa.ForeignKeyConstraint(["player_id"], ["players.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_user_follows_user_id", "user_follows", ["user_id"], unique=False)
    op.create_index(
        "uq_user_follows_user_team",
        "user_follows",
        ["user_id", "team_id"],
        unique=True,
        postgresql_where=sa.text("team_id IS NOT NULL"),
        sqlite_where=sa.text("team_id IS NOT NULL"),
    )
    op.create_index(
        "uq_user_follows_user_player",
        "user_follows",
        ["user_id", "player_id"],
        unique=True,
        postgresql_where=sa.text("player_id IS NOT NULL"),
        sqlite_where=sa.text("player_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_user_follows_user_player",
        table_name="user_follows",
        postgresql_where=sa.text("player_id IS NOT NULL"),
        sqlite_where=sa.text("player_id IS NOT NULL"),
    )
    op.drop_index(
        "uq_user_follows_user_team",
        table_name="user_follows",
        postgresql_where=sa.text("team_id IS NOT NULL"),
        sqlite_where=sa.text("team_id IS NOT NULL"),
    )
    op.drop_index("ix_user_follows_user_id", table_name="user_follows")
    op.drop_table("user_follows")
