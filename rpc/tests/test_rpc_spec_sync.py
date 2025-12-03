import json
from pathlib import Path

from rpc import methods


def test_spec_methods_are_registered():
    spec_path = Path("spec/openrpc.json")
    spec_doc = json.loads(spec_path.read_text())
    spec_methods = {m["name"] for m in spec_doc.get("methods", []) if "name" in m}

    registry = methods.get_registry()
    registered = set(registry.keys())

    missing = sorted(spec_methods - registered)
    assert not missing, f"Missing RPC implementations for spec methods: {missing}"
