"""Tests for on-disk season-table cache helpers."""

from __future__ import annotations

import pickle
from pathlib import Path

import pandas as pd

from baseball_analyze.data.cache_utils import load_or_compute


def test_load_or_compute_returns_cached_value(tmp_path: Path) -> None:
    calls = {"n": 0}

    def compute() -> pd.Series:
        calls["n"] += 1
        return pd.Series({"NYY": 3.5, "BOS": 4.1})

    first = load_or_compute("test_ns", {"season": 2024}, compute, cache_dir=tmp_path)
    second = load_or_compute("test_ns", {"season": 2024}, compute, cache_dir=tmp_path)

    assert first.equals(second)
    assert calls["n"] == 1


def test_load_or_compute_recovers_from_corrupt_pickle(tmp_path: Path) -> None:
    calls = {"n": 0}

    def compute() -> pd.Series:
        calls["n"] += 1
        return pd.Series({"NYY": 3.2})

    # Seed a valid cache entry, then overwrite the pickle with garbage.
    load_or_compute("test_ns", {"season": 2025}, compute, cache_dir=tmp_path)
    assert calls["n"] == 1

    cache_files = list((tmp_path / "test_ns").glob("*.pkl"))
    assert len(cache_files) == 1
    cache_files[0].write_bytes(b"not-a-valid-pickle")

    recovered = load_or_compute("test_ns", {"season": 2025}, compute, cache_dir=tmp_path)
    assert recovered["NYY"] == 3.2
    assert calls["n"] == 2


def test_load_or_compute_recovers_from_unpickling_typeerror(tmp_path: Path) -> None:
    """Simulate a pandas StringDtype version skew failure on load."""
    calls = {"n": 0}

    def compute() -> pd.Series:
        calls["n"] += 1
        return pd.Series({"NYY": 2.9})

    load_or_compute("test_ns", {"season": 2023}, compute, cache_dir=tmp_path)
    cache_files = list((tmp_path / "test_ns").glob("*.pkl"))
    assert len(cache_files) == 1

    class _Boom:
        def __reduce__(self):
            def _raise():
                raise TypeError(
                    "StringDtype.__init__() takes from 1 to 2 positional arguments but 3 were given"
                )

            return (_raise, ())

    with cache_files[0].open("wb") as f:
        pickle.dump(_Boom(), f)

    recovered = load_or_compute("test_ns", {"season": 2023}, compute, cache_dir=tmp_path)
    assert recovered["NYY"] == 2.9
    assert calls["n"] == 2
