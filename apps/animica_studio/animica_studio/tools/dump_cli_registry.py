from __future__ import annotations

from animica_studio.services.cli_capabilities import refresh_cli_registry, get_cli_ops
from animica_studio.services.cli_ops import CliOperation
from animica_studio.storage.config import load_config


def main() -> None:
    config = load_config()
    registry = refresh_cli_registry(config)
    ops = get_cli_ops(config)

    print(f"CLI path: {registry.cli_path or '<unknown>'}")
    print("Top-level commands:", ", ".join(registry.top_level_commands()) or "<none>")
    print()

    for op in CliOperation:
        try:
            path = ops.selected_path(op)
            opts = registry.options_for(path)
            print(f"{op.value}: {' '.join(path)}")
            if opts:
                print(f"  options: {', '.join(opts)}")
        except Exception as exc:  # noqa: BLE001
            print(f"{op.value}: <unsupported> ({exc})")


if __name__ == "__main__":
    main()
