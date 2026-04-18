from __future__ import annotations

import argparse

from animica.bridge_banm.api import create_app, seed_admin_user
from animica.bridge_banm.config import load_config


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed BANM bridge admin user")
    parser.add_argument("--username", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--role", default="admin")
    args = parser.parse_args()

    app = create_app(load_config())
    seed_admin_user(app=app, username=args.username, password=args.password, role=args.role)
    print(f"seeded admin user: {args.username} ({args.role})")


if __name__ == "__main__":
    main()

