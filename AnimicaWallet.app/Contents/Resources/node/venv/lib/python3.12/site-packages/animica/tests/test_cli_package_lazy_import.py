from __future__ import annotations

import importlib
import sys


def test_animica_cli_package_does_not_import_main_eagerly() -> None:
    sys.modules.pop("animica.cli.main", None)
    sys.modules.pop("animica.cli", None)

    importlib.import_module("animica.cli")

    assert "animica.cli.main" not in sys.modules


def test_importing_wallet_submodule_does_not_import_cli_main() -> None:
    sys.modules.pop("animica.cli.main", None)
    sys.modules.pop("animica.cli", None)

    importlib.import_module("animica.cli.wallet")

    assert "animica.cli.main" not in sys.modules
