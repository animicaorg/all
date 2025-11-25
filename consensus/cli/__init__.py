"""
Animica consensus CLI package.

Contains small command-line tools used to explore/benchmark PoIES and related
consensus components. Tools are intentionally lightweight and avoid importing
the full node to keep start-up times minimal.

Modules
-------
- bench_poies: PoIES scoring benchmark. Entrypoint: :func:`bench_poies.main`.

Convenience
-----------
We re-export the `bench_poies.main` entrypoint as `bench_poies_main` so you can:

    python -c "from consensus.cli import bench_poies_main; bench_poies_main(['simulate','--theta','5000000'])"
"""

from __future__ import annotations

__all__ = ["bench_poies_main"]

try:
    # Re-export bench entrypoint to enable programmatic usage.
    from .bench_poies import main as bench_poies_main  # type: ignore
except Exception:  # pragma: no cover
    # Keep the package importable even if optional deps (like PyYAML) are missing.
    def bench_poies_main(argv=None) -> int:
        raise RuntimeError("consensus.cli.bench_poies is unavailable (optional deps missing?)")
