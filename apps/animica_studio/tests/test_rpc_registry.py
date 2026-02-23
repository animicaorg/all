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
    assert registry.list_methods("da") == ["DA.GETSTATUS", "da.putBlob"]


def test_rpc_registry_keeps_full_param_metadata() -> None:
    registry = RpcRegistry(
        {
            "methods": [
                {
                    "name": "da.putBlob",
                    "params": [
                        {"name": "namespace", "required": True, "schema": {"type": "integer"}},
                        {"name": "data", "required": True, "schema": {"type": "string"}},
                    ],
                    "result": {"name": "blob_id", "schema": {"type": "string"}},
                }
            ]
        }
    )

    spec = registry.get_param_spec("da.putBlob")
    assert [p["name"] for p in spec] == ["namespace", "data"]
    assert spec[0]["required"] is True
    assert spec[0]["schema_type"] == "integer"
