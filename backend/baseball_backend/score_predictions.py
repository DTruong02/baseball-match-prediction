"""CLI entrypoint to score stored predictions against final game outcomes."""

from __future__ import annotations

import argparse

from baseball_backend.db.session import get_session_factory
from baseball_backend.services.model_registry import ModelVersionNotFoundError
from baseball_backend.services.performance_service import score_and_store_active_model


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Score stored predictions against final game outcomes.",
    )
    parser.parse_args()

    session = get_session_factory()()
    try:
        model_version = score_and_store_active_model(session)
        metrics = model_version.production_metrics or {}
        print(
            f"Scored model {model_version.run_id}: "
            f"n_games={metrics.get('n_games', 0)}, "
            f"accuracy={metrics.get('accuracy')}, "
            f"log_loss={metrics.get('log_loss')}"
        )
    except ModelVersionNotFoundError as exc:
        print(f"Error: {exc}")
        raise SystemExit(1) from exc
    finally:
        session.close()


if __name__ == "__main__":
    main()
