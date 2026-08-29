"""CLI entrypoint to register Stage 1 training artifacts as ModelVersion rows."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from baseball_backend.db.models import ModelVersionKind
from baseball_backend.db.session import get_session_factory
from baseball_backend.services.model_registry import ArtifactError, register_model_from_run
from baseball_backend.settings import get_settings


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Register a Stage 1 training run artifact directory as a ModelVersion.",
    )
    parser.add_argument("run_id", help="Run id directory name under artifacts/")
    parser.add_argument(
        "--artifacts-root",
        type=Path,
        default=None,
        help="Path to artifacts root (default: artifacts/ or ARTIFACTS_ROOT)",
    )
    parser.add_argument(
        "--activate",
        action="store_true",
        help="Set this model active and archive other active models of the same kind",
    )
    parser.add_argument(
        "--kind",
        choices=[kind.value for kind in ModelVersionKind],
        default=ModelVersionKind.PREGAME.value,
        help="Model kind (default: pregame)",
    )
    args = parser.parse_args()

    settings = get_settings()
    artifacts_root = args.artifacts_root or settings.artifacts_root

    session = get_session_factory()()
    try:
        model_version = register_model_from_run(
            session,
            args.run_id,
            artifacts_root=artifacts_root,
            kind=args.kind,
            activate=args.activate,
        )
    except ArtifactError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    finally:
        session.close()

    print(
        f"Registered {model_version.run_id} "
        f"(id={model_version.id}, kind={model_version.kind}, status={model_version.status})"
    )
    print(f"  artifact_path: {model_version.artifact_path}")


if __name__ == "__main__":
    main()
