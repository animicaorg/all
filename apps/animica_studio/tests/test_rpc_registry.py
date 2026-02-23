from __future__ import annotations

from animica_studio.services.rpc_client import RpcRegistry


def test_rpc_registry_normalizes_and_resolves_dotted_and_underscore_variants() -> None:
    registry = RpcRegistry(
        {
            "methods": [
                {"name": "DA.GETSTATUS", "params": []},
                {"name": "da.putBlob", "params": []},
                {"name": "aicf_listJobs", "params": []},
            ]
        }
    )

    assert registry.resolve_any(["da_getStatus"]) == "DA.GETSTATUS"
    assert registry.resolve_any(["da.getStatus"]) == "DA.GETSTATUS"
    assert registry.has_any(["da"]) is True
    assert registry.dump_methods("da") == ["DA.GETSTATUS", "da.putBlob"]
