"""CLI entrypoint to generate missing pregame predictions for a date."""

from __future__ import annotations

import argparse
from datetime import date

from baseball_backend.db.session import get_session_factory
from baseball_backend.services.prediction_service import generate_missing_predictions_for_date


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate missing pregame predictions for games on a date.",
    )
    parser.add_argument(
        "--date",
        default=date.today().isoformat(),
        help="Game date in YYYY-MM-DD format (default: today)",
    )
    args = parser.parse_args()

    session = get_session_factory()()
    try:
        count = generate_missing_predictions_for_date(session, args.date)
        print(f"Generated {count} prediction(s) for {args.date}")
    finally:
        session.close()


if __name__ == "__main__":
    main()
