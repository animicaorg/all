from __future__ import annotations

from importlib.metadata import version


def test_backend_runtime_imports() -> None:
    import fastapi  # noqa: F401
    import prometheus_client  # noqa: F401
    import rpc  # noqa: F401
    import rpc.server  # noqa: F401
    import animica.stratum_pool.cli  # noqa: F401

    assert version("fastapi")
    assert version("prometheus-client")
