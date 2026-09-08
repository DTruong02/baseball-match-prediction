"""Add hot-path indexes identified in Stage 7.4 review."""

from typing import Sequence, Union

from alembic import op

revision: str = "20260908_0008"
down_revision: Union[str, Sequence[str], None] = "20260908_0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "ix_model_versions_kind_status",
        "model_versions",
        ["kind", "status"],
        unique=False,
    )
    op.create_index("ix_players_team_id", "players", ["team_id"], unique=False)
    op.create_index(
        "ix_games_status_game_date",
        "games",
        ["status", "game_date"],
        unique=False,
    )
    op.create_index(
        "ix_predictions_model_version_id",
        "predictions",
        ["model_version_id"],
        unique=False,
    )
    op.create_index(
        "ix_teams_abbreviation",
        "teams",
        ["abbreviation"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_teams_abbreviation", table_name="teams")
    op.drop_index("ix_predictions_model_version_id", table_name="predictions")
    op.drop_index("ix_games_status_game_date", table_name="games")
    op.drop_index("ix_players_team_id", table_name="players")
    op.drop_index("ix_model_versions_kind_status", table_name="model_versions")
