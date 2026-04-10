import inspect
from pathlib import Path
from typing import Any, Callable, Dict, Optional

import pytest

import proofs.registry as reg
from proofs import types as ptypes
from proofs.tests import schema_path  # local helper ensures schemas dir exists

# ---------- helpers to tolerate minor API/name drift in registry.py ----------


def _first_attr(obj: Any, *names: str) -> Any:
    for n in names:
        if hasattr(obj, n):
            return getattr(obj, n)
    raise AttributeError(f"None of the expected attributes exist: {names!r}")


def _schema_map() -> Dict[int, Any]:
    """
    Return the mapping of proof-type → schema descriptor.
    Accepts several plausible names to keep tests stable as the module evolves.
    """
    try:
        m = _first_attr(
            reg,
            "TYPE_TO_SCHEMA",
            "SCHEMA_BY_TYPE",
            "SCHEMAS",
            "TYPE_SCHEMA_MAP",
        )
    except AttributeError:
        pytest.fail(
            "proofs.registry must expose a mapping of type→schema (e.g., TYPE_TO_SCHEMA)"
        )
    assert isinstance(m, dict), "schema map must be a dict"
    return m  # type: ignore[return-value]


def _verifier_map() -> Dict[int, Callable[..., Any]]:
    """
    Return the mapping of proof-type → verifier callable.
    """
    try:
        m = _first_attr(reg, "VERIFIERS", "TYPE_TO_VERIFIER", "VERIFIER_BY_TYPE")
    except AttributeError:
        # Fall back to a getter function if present.
        getter = getattr(reg, "get_verifier", None) or getattr(
            reg, "verifier_for", None
        )
        if getter is None:
            pytest.fail(
                "proofs.registry must expose verifiers via VERIFIERS (dict) "
                "or a get_verifier(type_id) function"
            )
        # Build a synthetic view for known types
        return {
            int(ptypes.ProofType.HASHSHARE): getter(ptypes.ProofType.HASHSHARE),
            int(ptypes.ProofType.AI): getter(ptypes.ProofType.AI),
            int(ptypes.ProofType.QUANTUM): getter(ptypes.ProofType.QUANTUM),
            int(ptypes.ProofType.STORAGE): getter(ptypes.ProofType.STORAGE),
            int(ptypes.ProofType.VDF): getter(ptypes.ProofType.VDF),
        }
    assert isinstance(m, dict), "verifier map must be a dict"
    # Basic sanity: all values callable
    for k, v in m.items():
        assert callable(v), f"Verifier for type {k} must be callable"
    return m  # type: ignore[return-value]


def _schema_path_from_descriptor(desc: Any) -> Optional[Path]:
    """
    The registry may hold plain filenames, absolute Paths, or small objects.
    We normalize to a Path if we can guess the filename.
    """
    if isinstance(desc, (str, Path)):
        name = Path(str(desc)).name
        # If desc is only a name, resolve relative to proofs/schemas
        return schema_path(name)
    if isinstance(desc, dict):
        # Common keys that might carry a filename
        for key in ("file", "filename", "path", "schema", "cddl"):
            if key in desc and desc[key]:
                return _schema_path_from_descriptor(desc[key])
    # Unknown descriptor shape
    return None


# ---------- tests ----------


def test_registry_exposes_known_types_and_verifiers() -> None:
    vmap = _verifier_map()
    # Ensure the five canonical types exist & map to callables
    for t in (
        ptypes.ProofType.HASHSHARE,
        ptypes.ProofType.AI,
        ptypes.ProofType.QUANTUM,
        ptypes.ProofType.STORAGE,
        ptypes.ProofType.VDF,
    ):
        tid = int(t)
        assert tid in vmap, f"Missing verifier for proof type {t.name} ({tid})"
        fn = vmap[tid]
        assert callable(fn), f"Verifier for {t.name} must be callable"
        # Optional: check signature is reasonable (accepts envelope or bytes)
        sig = inspect.signature(fn)
        assert len(sig.parameters) >= 1, "Verifier should take at least one argument"


def test_schema_mapping_points_to_existing_files() -> None:
    smap = _schema_map()

    expected_suffix = {
        int(ptypes.ProofType.HASHSHARE): "hashshare.cddl",
        int(ptypes.ProofType.AI): "ai_attestation.schema.json",
        int(ptypes.ProofType.QUANTUM): "quantum_attestation.schema.json",
        int(ptypes.ProofType.STORAGE): "storage.cddl",
        int(ptypes.ProofType.VDF): "vdf.cddl",
    }

    # Generic envelope schema must also exist alongside the type-specific ones
    env = schema_path("proof_envelope.cddl")
    assert env.exists(), f"Missing generic envelope schema: {env}"

    # Now check each type-specific mapping resolves to a file and matches expected suffix
    for tid, suffix in expected_suffix.items():
        assert tid in smap, f"Schema map missing entry for type id {tid}"
        sp = _schema_path_from_descriptor(smap[tid])
        assert (
            sp is not None
        ), f"Could not resolve schema path for type id {tid} (desc={smap[tid]!r})"
        assert sp.exists(), f"Schema path not found on disk: {sp}"
        assert sp.name.lower().endswith(
            suffix
        ), f"Schema for type {tid} should end with {suffix}, got {sp.name}"


def test_register_and_override_round_trip(monkeypatch) -> None:
    """
    If the registry exposes a 'register' API, ensure we can register a temporary
    verifier & schema and then restore the original.
    """
    register = getattr(reg, "register", None)
    if register is None:
        pytest.skip("registry.register() not exposed; skip override test")

    # Pick an out-of-range test type id to avoid collisions
    TEST_TYPE = 255_001

    def dummy_verifier(envelope: Any) -> Any:
        return {"ok": True, "units": 0}

    tmp_schema = "dummy_test_schema.cddl"

    # Remember original (if any)
    smap = _schema_map()
    vmap = _verifier_map()
    orig_schema = smap.get(TEST_TYPE)
    orig_verifier = vmap.get(TEST_TYPE)

    # Register
    register(TEST_TYPE, dummy_verifier, tmp_schema)  # type: ignore[misc]

    # Validate presence
    smap2 = _schema_map()
    vmap2 = _verifier_map()
    assert TEST_TYPE in vmap2 and callable(
        vmap2[TEST_TYPE]
    ), "dummy verifier should be installed"
    sp = _schema_path_from_descriptor(smap2[TEST_TYPE])
    assert sp is not None and sp.name == Path(tmp_schema).name

    # Cleanup: restore original maps if an unregister() helper exists, use it;
    # otherwise monkeypatch the dicts directly.
    unregister = getattr(reg, "unregister", None)
    if unregister:
        unregister(TEST_TYPE)  # type: ignore[misc]
    else:
        # Best-effort restore
        if orig_verifier is None:
            vmap2.pop(TEST_TYPE, None)
        else:
            vmap2[TEST_TYPE] = orig_verifier
        if orig_schema is None:
            smap2.pop(TEST_TYPE, None)
        else:
            smap2[TEST_TYPE] = orig_schema


def test_all_schema_files_are_well_located_on_disk() -> None:
    """
    Walk the schema map and ensure every referenced file actually exists in proofs/schemas/.
    This catches path typos in the registry.
    """
    smap = _schema_map()
    missing = []
    for tid, desc in smap.items():
        sp = _schema_path_from_descriptor(desc)
        if sp is None or not sp.exists():
            missing.append((tid, desc))
    assert (
        not missing
    ), f"Some schema entries are missing or could not be resolved: {missing}"
