from __future__ import annotations

import argparse
from pathlib import Path

from alembic import command
from alembic.config import Config


def _alembic_config() -> Config:
    cfg = Config(str(Path("python/animica/bridge_banm/alembic.ini")))
    return cfg


def main() -> None:
    parser = argparse.ArgumentParser(description="BANM bridge Alembic migrations")
    parser.add_argument("action", choices=["upgrade", "downgrade", "current", "history"])
    parser.add_argument("revision", nargs="?", default="head")
    args = parser.parse_args()

    cfg = _alembic_config()
    if args.action == "upgrade":
        command.upgrade(cfg, args.revision)
    elif args.action == "downgrade":
        command.downgrade(cfg, args.revision)
    elif args.action == "current":
        command.current(cfg)
    elif args.action == "history":
        command.history(cfg)


if __name__ == "__main__":
    main()

