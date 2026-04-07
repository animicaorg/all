from __future__ import annotations


def test_backend_runtime_imports() -> None:
    import fastapi  # noqa: F401
    import prometheus_client  # noqa: F401
    import rpc.server  # noqa: F401
    import ena.services.ena_node.main  # noqa: F401
    import animica.stratum_pool.cli  # noqa: F401
