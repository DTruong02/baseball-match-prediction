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


@dataclass(frozen=True)
class GroundedChatResult:
    """Final assistant text plus optional tool-call trace."""

    answer: str
    tool_trace: list[dict[str, Any]]


def get_client_config(
    *,
    base_url: str | None = None,
    api_key: str | None = None,
    model: str | None = None,
) -> LLMClientConfig:
    """Resolve LLM client settings from explicit args and environment."""
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


# Backward-compatible alias used by older call sites / tests.
_get_client_config = get_client_config


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

EXPLAIN_SYSTEM_PROMPT = """You explain pregame model leans for baseball games.

Rules:
- Never invent probabilities, feature values, or stats.
- Only use numbers present in the provided grounded JSON context.
- Describe which side the model favors and why, using the feature diffs and notes.
- If features are missing, say so and stick to the probabilities that are present.
- Keep the explanation concise (a short paragraph).
"""

SUMMARIZE_SYSTEM_PROMPT = """You summarize baseball games using only provided grounded data.

Rules:
- Never invent scores, probabilities, play events, or stats.
- Only use fields present in the grounded JSON context.
- Cover current status/score, pregame line if present, live win probability if present, and notable recent events.
- If a field is missing, omit it rather than guessing.
- Keep the summary concise (a short paragraph).
"""


def _require_openai_client():
    try:
        from openai import OpenAI  # type: ignore
    except Exception as e:  # pragma: no cover
        raise RuntimeError(
            'Chat extra not installed. Install with: pip install -e ".[chat]"'
        ) from e
    return OpenAI


def run_grounded_chat(
    messages: list[dict[str, Any]],
    *,
    model_path: Path | None = None,
    cache_dir: Path | None = None,
    config: LLMClientConfig,
    tools: list[dict[str, Any]] | None = None,
    dispatch: dict[str, Callable[..., Any]] | None = None,
    max_tool_rounds: int = 8,
) -> GroundedChatResult:
    """
    Run one grounded chat turn (optionally with tool calling).

    When ``tools`` / ``dispatch`` are omitted and ``model_path`` is set, the
    default schedule + ``predict_games`` tools are used. Pass empty lists to
    disable tools for context-only completions (explain / summarize).
    """
    OpenAI = _require_openai_client()
    client = OpenAI(api_key=config.api_key, base_url=config.base_url)

    if tools is None:
        if model_path is None:
            raise ValueError("model_path is required when using default chat tools")
        tools = _tool_schemas()
        dispatch = _tool_dispatch(model_path, cache_dir)
    elif dispatch is None:
        dispatch = {}

    # Mutate the caller's message list so REPL / multi-turn callers keep tool history.
    tool_trace: list[dict[str, Any]] = []

    for _ in range(max(1, max_tool_rounds)):
        create_kwargs: dict[str, Any] = {
            "model": config.model,
            "messages": messages,
        }
        if tools:
            create_kwargs["tools"] = tools
            create_kwargs["tool_choice"] = "auto"

        resp = client.chat.completions.create(**create_kwargs)
        msg = resp.choices[0].message

        assistant_content = getattr(msg, "content", None)
        tool_calls = getattr(msg, "tool_calls", None)

        if tool_calls and tools:
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
                    if fn not in dispatch:
                        out: Any = {"error": f"unknown_tool:{fn}"}
                    else:
                        out = dispatch[fn](**args)
                except Exception as e:
                    out = {"error": str(e)}

                tool_trace.append({"name": fn, "arguments": args, "result": out})
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "name": fn,
                        "content": json.dumps(out),
                    }
                )
            continue

        answer = (assistant_content or "").strip()
        messages.append({"role": "assistant", "content": assistant_content or ""})
        return GroundedChatResult(answer=answer, tool_trace=tool_trace)

    raise RuntimeError("Grounded chat exceeded max tool rounds without a final answer.")


def run_repl(
    *,
    model_path: Path,
    cache_dir: Path | None,
    base_url: str | None,
    api_key: str | None,
    llm_model: str | None,
) -> None:
    cfg = get_client_config(base_url=base_url, api_key=api_key, model=llm_model)
    # Eagerly validate the optional dependency before entering the loop.
    _require_openai_client()

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
        result = run_grounded_chat(
            messages,
            model_path=model_path,
            cache_dir=cache_dir,
            config=cfg,
        )
        print(result.answer)
