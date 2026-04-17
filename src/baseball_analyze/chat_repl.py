"""Terminal chat REPL backed by an LLM with tool calling."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from baseball_analyze import chat_tools


@dataclass(frozen=True)
class LLMClientConfig:
    api_key: str
    base_url: str | None
    model: str


def _get_client_config(
    *,
    base_url: str | None,
    api_key: str | None,
    model: str | None,
) -> LLMClientConfig:
    env_base = os.getenv("LLM_BASE_URL") or os.getenv("OPENAI_BASE_URL")
    env_key = os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY")
    env_model = os.getenv("LLM_MODEL") or os.getenv("OPENAI_MODEL")

    final_base = base_url or (env_base.strip() if env_base else None)
    final_model = (model or env_model or ("gpt-4o-mini" if final_base is None else "llama3.1")).strip()

    final_key = (api_key or env_key or "").strip()
    if not final_key:
        if final_base is not None:
            # Many local servers ignore the key, but the SDK typically requires a non-empty string.
            final_key = "ollama"
        else:
            raise RuntimeError("Missing OPENAI_API_KEY (or LLM_API_KEY).")

    return LLMClientConfig(api_key=final_key, base_url=final_base, model=final_model)


def _tool_schemas() -> list[dict[str, Any]]:
    # OpenAI-compatible tool schema (works for OpenAI and many local servers).
    return [
        {
            "type": "function",
            "function": {
                "name": "resolve_date",
                "description": "Resolve 'today', 'tomorrow', or a date string to YYYY-MM-DD.",
                "parameters": {
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                    "required": ["text"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "list_games_for_date",
                "description": "List MLB games for a given YYYY-MM-DD date.",
                "parameters": {
                    "type": "object",
                    "properties": {"date": {"type": "string"}},
                    "required": ["date"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "find_games_on_date",
                "description": "Find MLB games on a date by away/home team hints (abbrev or name).",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "date": {"type": "string"},
                        "away_team": {"type": ["string", "null"]},
                        "home_team": {"type": ["string", "null"]},
                    },
                    "required": ["date"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "predict_games",
                "description": "Predict P(home win) for one or more gamePks using the local sklearn model artifact.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "model_path": {"type": "string"},
                        "game_pks": {"type": "array", "items": {"type": "integer"}},
                        "cache_dir": {"type": ["string", "null"]},
                    },
                    "required": ["model_path", "game_pks"],
                },
            },
        },
    ]


def _tool_dispatch(
    model_path: Path, default_cache_dir: Path | None
) -> dict[str, Callable[..., Any]]:
    def predict_games(
        *,
        model_path: str,
        game_pks: list[int],
        cache_dir: str | None = None,
    ) -> Any:
        # We intentionally ignore any model_path the LLM tried to supply and
        # always use the one from the CLI invocation to avoid path injection.
        effective_cache = cache_dir or (str(default_cache_dir) if default_cache_dir else None)
        return chat_tools.predict_games(
            model_path=str(model_path),
            game_pks=game_pks,
            cache_dir=effective_cache,
        )

    return {
        "resolve_date": chat_tools.resolve_date,
        "list_games_for_date": chat_tools.list_games_for_date,
        "find_games_on_date": chat_tools.find_games_on_date,
        "predict_games": lambda **kwargs: predict_games(
            model_path=str(model_path), **{k: v for k, v in kwargs.items() if k != "model_path"}
        ),
    }


SYSTEM_PROMPT = """You are a baseball pregame prediction assistant.

Rules:
- Never invent probabilities. Only use numbers returned by the tool results.
- When asked about a specific date or matchup, call tools to find the right gamePk(s), then call predict_games.
- If multiple games match, ask a brief clarifying question and list the options with game_pk.
- If a game is postponed/cancelled or not found, say so.
"""


def run_repl(
    *,
    model_path: Path,
    cache_dir: Path | None,
    base_url: str | None,
    api_key: str | None,
    llm_model: str | None,
) -> None:
    try:
        from openai import OpenAI  # type: ignore
    except Exception as e:  # pragma: no cover
        raise RuntimeError(
            'Chat extra not installed. Install with: pip install -e ".[chat]"'
        ) from e

    cfg = _get_client_config(base_url=base_url, api_key=api_key, model=llm_model)
    client = OpenAI(api_key=cfg.api_key, base_url=cfg.base_url)

    tools = _tool_schemas()
    dispatch = _tool_dispatch(model_path, cache_dir)

    messages: list[dict[str, Any]] = [{"role": "system", "content": SYSTEM_PROMPT}]

    print("baseball-analyze chat (type 'exit' to quit)")
    while True:
        try:
            user = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return

        if not user:
            continue
        if user.lower() in {"exit", "quit"}:
            return

        messages.append({"role": "user", "content": user})

        while True:
            resp = client.chat.completions.create(
                model=cfg.model,
                messages=messages,
                tools=tools,
                tool_choice="auto",
            )
            msg = resp.choices[0].message

            assistant_content = getattr(msg, "content", None)
            tool_calls = getattr(msg, "tool_calls", None)

            if tool_calls:
                messages.append(
                    {
                        "role": "assistant",
                        "content": assistant_content,
                        "tool_calls": [tc.model_dump() for tc in tool_calls],
                    }
                )
                for tc in tool_calls:
                    fn = tc.function.name
                    raw_args = tc.function.arguments or "{}"
                    try:
                        args = json.loads(raw_args)
                    except json.JSONDecodeError:
                        args = {}

                    try:
                        out = dispatch[fn](**args)
                    except Exception as e:
                        out = {"error": str(e)}

                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "name": fn,
                            "content": json.dumps(out),
                        }
                    )
                continue

            messages.append({"role": "assistant", "content": assistant_content or ""})
            print((assistant_content or "").strip())
            break

