"""Simple on-disk cache for expensive pybaseball / DataFrame loads."""

from __future__ import annotations

import hashlib
import json
import pickle
from pathlib import Path
from typing import Any, Callable, TypeVar

T = TypeVar("T")


def default_cache_dir() -> Path:
    return Path.cwd() / "cache"


def _key_path(cache_dir: Path, namespace: str, key: str) -> Path:
    safe = hashlib.sha256(key.encode()).hexdigest()[:24]
    d = cache_dir / namespace
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{safe}.pkl"


def load_or_compute(
    namespace: str,
    key_parts: dict[str, Any],
    compute: Callable[[], T],
    cache_dir: Path | None = None,
) -> T:
    """Load pickle from cache if present; otherwise compute and save."""
    base = cache_dir or default_cache_dir()
    key = json.dumps(key_parts, sort_keys=True, default=str)
    path = _key_path(base, namespace, key)
    if path.exists():
        with path.open("rb") as f:
            return pickle.load(f)
    value = compute()
    with path.open("wb") as f:
        pickle.dump(value, f)
    return value
