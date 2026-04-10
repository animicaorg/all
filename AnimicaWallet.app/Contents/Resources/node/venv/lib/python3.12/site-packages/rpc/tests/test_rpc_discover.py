import typing as t

from rpc.tests import new_test_client, rpc_call


def _method_names(doc: dict[str, t.Any]) -> list[str]:
    names: list[str] = []
    for m in doc.get("methods", []):
        if isinstance(m, str):
            names.append(m)
        elif isinstance(m, dict) and "name" in m:
            names.append(str(m["name"]))
    return names


def test_rpc_discover_serves_openrpc_doc():
    client, _, _ = new_test_client()
    res = rpc_call(client, "rpc.discover")
    doc = res["result"]

    assert isinstance(doc, dict)
    assert doc.get("openrpc")

    names = _method_names(doc)
    # Should surface at least a few core methods
    assert "chain.getChainId" in names
    assert "rpc.discover" in names
