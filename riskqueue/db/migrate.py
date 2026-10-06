"""Run packaged Alembic migrations from any working directory."""

from __future__ import annotations

import argparse
from pathlib import Path

from alembic import command
from alembic.config import Config


def main() -> None:
    parser = argparse.ArgumentParser(description="Run RiskQueue operational database migrations")
    parser.add_argument("action", choices=["upgrade", "current"], nargs="?", default="upgrade")
    parser.add_argument("revision", nargs="?", default="head")
    args = parser.parse_args()
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).parents[1] / "migrations"))
    if args.action == "current":
        command.current(config)
    else:
        command.upgrade(config, args.revision)


if __name__ == "__main__":
    main()
