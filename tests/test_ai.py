"""Tests for Stage 6.5 grounded AI endpoints."""

from __future__ import annotations

from collections.abc import Generator
from datetime import date
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import JSON, create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from baseball_analyze.chat_repl import GroundedChatResult
from baseball_backend.db.base import Base
from baseball_backend.db.models import (
    Game,
    GameEvent,
    ModelVersion,
    Player,
    Prediction,
    Team,
    User,
)
from baseball_backend.db.session import get_db
from baseball_backend.main import app

SAMPLE_TEAMS = [
    {"id": 111, "abbreviation": "BOS", "name": "Red Sox", "city": "Boston"},
    {"id": 147, "abbreviation": "NYY", "name": "Yankees", "city": "New York"},
]
SAMPLE_PITCHERS = [
    {"id": 669203, "full_name": "Corbin Burnes", "team_id": 111, "primary_position": "P"},
    {"id": 592866, "full_name": "Trevor Williams", "team_id": 147, "primary_position": "P"},
]


@pytest.fixture
def db_session() -> Generator[Session, None, None]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    jsonb_columns: list = []
    for table in (ModelVersion.__table__, Prediction.__table__, GameEvent.__table__):
        for column in table.columns:
            if isinstance(column.type, JSONB):
                jsonb_columns.append(column)
                column.type = JSON()

    tables = [
        User.__table__,
        Team.__table__,
        Player.__table__,
        Game.__table__,
        ModelVersion.__table__,
        Prediction.__table__,
        GameEvent.__table__,
    ]
    Base.metadata.create_all(bind=engine, tables=tables)
    session = sessionmaker(bind=engine, autocommit=False, autoflush=False)()
    try:
        for team_data in SAMPLE_TEAMS:
            session.add(Team(**team_data))
        for pitcher_data in SAMPLE_PITCHERS:
            session.add(Player(**pitcher_data))
        session.add(
            Game(
                game_pk=778285,
                game_date=date(2025, 4, 6),
                season=2025,
                status="Preview",
                detailed_state="Scheduled",
                home_team_id=147,
                away_team_id=111,
                venue_id=3313,
                venue_name="Yankee Stadium",
                home_probable_pitcher_id=592866,
                away_probable_pitcher_id=669203,
            )
        )
        session.add(
            ModelVersion(
                run_id="ai-test-run",
                kind="pregame",
                status="active",
                artifact_path="/tmp/model.joblib",
                feature_columns=["diff_wrc_plus", "home_field"],
                metrics={},
            )
        )
        session.flush()
        model = session.query(ModelVersion).one()
        game = session.query(Game).filter_by(game_pk=778285).one()
        session.add(
            Prediction(
                game_id=game.id,
                model_version_id=model.id,
                home_win_proba=0.58,
                away_win_proba=0.42,
                features={"diff_wrc_plus": 8.0, "home_field": 1.0},
                notes="starter FIP missing for away",
            )
        )
        session.add(
            GameEvent(
                game_pk=778285,
                event_id="play-1",
                type="play",
                payload={"description": "Single to left", "is_scoring_play": False},
                sequence=1,
            )
        )
        session.commit()
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine, tables=tables)
        engine.dispose()
        for column in jsonb_columns:
            column.type = JSONB()


@pytest.fixture
def client(db_session: Session) -> Generator[TestClient, None, None]:
    def override_get_db() -> Generator[Session, None, None]:
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def auth_headers(client: TestClient) -> dict[str, str]:
    client.post(
        "/auth/register",
        json={"email": "ai-fan@example.com", "password": "secretpass"},
    )
    login = client.post(
        "/auth/login",
        data={"username": "ai-fan@example.com", "password": "secretpass"},
    )
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def test_ai_explain_requires_auth(client: TestClient) -> None:
    response = client.post("/ai/explain", json={"game_pk": 778285})
    assert response.status_code == 401


def test_ai_explain_grounded(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    from baseball_analyze.chat_repl import LLMClientConfig

    with (
        patch(
            "baseball_backend.services.ai_service.resolve_llm_config",
            return_value=LLMClientConfig(api_key="test", base_url=None, model="gpt-test"),
        ),
        patch(
            "baseball_backend.services.ai_service.run_grounded_chat",
            return_value=GroundedChatResult(
                answer="Model leans Yankees on wRC+ and home field.",
                tool_trace=[],
            ),
        ) as mocked,
    ):
        response = client.post(
            "/ai/explain",
            json={"game_pk": 778285},
            headers=auth_headers,
        )

    assert response.status_code == 200
    body = response.json()
    assert body["game_pk"] == 778285
    assert body["home_win_proba"] == 0.58
    assert body["away_win_proba"] == 0.42
    assert body["features"]["diff_wrc_plus"] == 8.0
    assert "Yankees" in body["explanation"]
    assert body["model_version"]["run_id"] == "ai-test-run"

    messages = mocked.call_args.args[0]
    user_content = messages[1]["content"]
    assert "0.58" in user_content
    assert "diff_wrc_plus" in user_content
    assert mocked.call_args.kwargs["tools"] == []


def test_ai_explain_missing_game(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    response = client.post(
        "/ai/explain",
        json={"game_pk": 999999},
        headers=auth_headers,
    )
    assert response.status_code == 404


def test_ai_summarize_game(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    from baseball_analyze.chat_repl import LLMClientConfig

    with (
        patch(
            "baseball_backend.services.ai_service.resolve_llm_config",
            return_value=LLMClientConfig(api_key="test", base_url=None, model="gpt-test"),
        ),
        patch(
            "baseball_backend.services.ai_service.run_grounded_chat",
            return_value=GroundedChatResult(
                answer="BOS at NYY is scheduled; model has NYY at 58%.",
                tool_trace=[],
            ),
        ) as mocked,
    ):
        response = client.post(
            "/ai/summarize-game",
            json={"game_pk": 778285},
            headers=auth_headers,
        )

    assert response.status_code == 200
    body = response.json()
    assert body["game_pk"] == 778285
    assert "scheduled" in body["summary"].lower() or "NYY" in body["summary"]

    user_content = mocked.call_args.args[0][1]["content"]
    assert "778285" in user_content
    assert "play-1" in user_content or "Single to left" in user_content


def test_ai_ask_uses_tools(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    from baseball_analyze.chat_repl import LLMClientConfig

    def _fake_chat(messages, **kwargs):
        assert kwargs.get("model_path") is not None
        # Default tools path (None) means chat_tools schemas are used.
        assert kwargs.get("tools") is None
        return GroundedChatResult(
            answer="Home win probability is 0.58 from predict_games.",
            tool_trace=[
                {
                    "name": "predict_games",
                    "arguments": {"game_pks": [778285]},
                    "result": [{"p_home_win": 0.58, "p_away_win": 0.42}],
                }
            ],
        )

    with (
        patch(
            "baseball_backend.services.ai_service.resolve_llm_config",
            return_value=LLMClientConfig(api_key="test", base_url=None, model="gpt-test"),
        ),
        patch(
            "baseball_backend.services.ai_service.run_grounded_chat",
            side_effect=_fake_chat,
        ),
    ):
        response = client.post(
            "/ai/ask",
            json={
                "question": "What is the Yankees home win probability?",
                "game_pk": 778285,
            },
            headers=auth_headers,
        )

    assert response.status_code == 200
    body = response.json()
    assert "0.58" in body["answer"]
    assert body["tool_trace"][0]["name"] == "predict_games"
    assert body["game_pk"] == 778285


def test_ai_ask_empty_question(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    response = client.post(
        "/ai/ask",
        json={"question": "   "},
        headers=auth_headers,
    )
    # Pydantic min_length or service ValueError → 422
    assert response.status_code == 422


def test_ai_llm_misconfigured(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    with patch(
        "baseball_backend.services.ai_service.resolve_llm_config",
        side_effect=__import__(
            "baseball_backend.services.ai_service", fromlist=["AiConfigError"]
        ).AiConfigError("Missing OPENAI_API_KEY (or LLM_API_KEY)."),
    ):
        response = client.post(
            "/ai/explain",
            json={"game_pk": 778285},
            headers=auth_headers,
        )
    assert response.status_code == 503
    assert "API_KEY" in response.json()["detail"]


def test_run_grounded_chat_dispatches_tools() -> None:
    """Unit-level: tool loop records results and returns final answer."""
    from pathlib import Path

    from baseball_analyze import chat_repl

    fake_message_with_tools = MagicMock()
    fake_message_with_tools.content = None
    fake_tc = MagicMock()
    fake_tc.id = "call_1"
    fake_tc.function.name = "resolve_date"
    fake_tc.function.arguments = '{"text": "today"}'
    fake_tc.model_dump.return_value = {
        "id": "call_1",
        "type": "function",
        "function": {"name": "resolve_date", "arguments": '{"text": "today"}'},
    }
    fake_message_with_tools.tool_calls = [fake_tc]

    fake_final = MagicMock()
    fake_final.content = "Date resolved."
    fake_final.tool_calls = None

    fake_choice1 = MagicMock()
    fake_choice1.message = fake_message_with_tools
    fake_choice2 = MagicMock()
    fake_choice2.message = fake_final

    fake_client = MagicMock()
    fake_client.chat.completions.create.side_effect = [
        MagicMock(choices=[fake_choice1]),
        MagicMock(choices=[fake_choice2]),
    ]
    fake_openai = MagicMock(return_value=fake_client)

    with patch.object(chat_repl, "_require_openai_client", return_value=fake_openai):
        with patch.object(
            chat_repl.chat_tools,
            "resolve_date",
            return_value="2025-04-06",
        ) as resolve:
            messages = [
                {"role": "system", "content": "sys"},
                {"role": "user", "content": "what is today?"},
            ]
            result = chat_repl.run_grounded_chat(
                messages,
                model_path=Path("/tmp/model.joblib"),
                cache_dir=None,
                config=chat_repl.LLMClientConfig(
                    api_key="test",
                    base_url=None,
                    model="gpt-test",
                ),
            )

    assert result.answer == "Date resolved."
    assert result.tool_trace[0]["name"] == "resolve_date"
    assert result.tool_trace[0]["result"] == "2025-04-06"
    resolve.assert_called_once()
