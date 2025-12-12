"""Module entrypoint for ``python -m animica``.

This forwards execution to the Typer-based CLI defined in ``animica.cli.main``
so the package can be invoked directly as a module.
"""

from animica.cli.main import main


if __name__ == "__main__":
    main()
