import copy
import json
import os
from importlib import import_module
from typing import Any, Callable, Tuple, Union

import pytest

# --- helpers ------------------------------------------------------------------


def _load_example_policy() -> Tuple[dict, str]:
    """
    Load the example alg-policy JSON and return (policy_dict, absolute_path).
    """
    here = os.path.abspath(os.path.dirname(__file__))
    policy_path = os.path.join(here, "..", "alg_policy", "example_policy.json")
    policy_path = os.path.normpath(policy_path)
    if not os.path.exists(policy_path):
        pytest.skip(f"example policy JSON not found at {policy_path}")
    with open(policy_path, "rb") as f:
        policy = json.loads(f.read().decode("utf-8"))
    return policy, policy_path


def _discover_builder() -> Callable[[Union[dict, str]], str]:
    """
    Find a callable in pq.alg_policy.build_root that takes either a policy dict or a file path
    and returns a hex SHA3-512 Merkle root (or a tuple with root as first element).
    """
    try:
        mod = import_module("pq.alg_policy.build_root")
    except Exception as e:
        pytest.skip(f"cannot import pq.alg_policy.build_root: {e}")

    candidate_names = (
        "compute_root",
        "build_root",
        "root_from_policy",
        "alg_policy_root",
    )
    funcs = [getattr(mod, n, None) for n in candidate_names]
    funcs = [f for f in funcs if callable(f)]
    if not funcs:
        pytest.skip("no suitable function in pq.alg_policy.build_root")

    def _runner(arg: Union[dict, str]) -> str:
        last_err: Exception | None = None
        for fn in funcs:
            # try plain positional
            try:
                out = fn(arg)  # type: ignore[misc]
                root = out[0] if isinstance(out, (tuple, list)) else out
                if not isinstance(root, (bytes, str)):
                    continue
                if isinstance(root, bytes):
                    root = "0x" + root.hex()
                return root
            except TypeError as e:
                last_err = e
            # try keyword form
            try:
                out = fn(policy=arg)  # type: ignore[misc]
                root = out[0] if isinstance(out, (tuple, list)) else out
                if isinstance(root, bytes):
                    root = "0x" + root.hex()
                return root
            except TypeError as e:
                last_err = e
        raise RuntimeError(f"no callable accepted provided arg; last error: {last_err}")

    return _runner


def _is_hex512(root: str) -> bool:
    return (
        isinstance(root, str)
        and root.startswith("0x")
        and len(root) == 130
        and all(c in "0123456789abcdef" for c in root[2:])
    )


# --- tests --------------------------------------------------------------------


def test_root_deterministic_and_hex_shape():
    policy, policy_path = _load_example_policy()
    builder = _discover_builder()

    # Try dict input first; if it fails, fall back to file-path input.
    try:
        r1 = builder(policy)
        r2 = builder(copy.deepcopy(policy))
    except RuntimeError:
        r1 = builder(policy_path)
        r2 = builder(policy_path)

    assert r1 == r2, "root must be deterministic for identical policy input"
    assert _is_hex512(r1), "root must be a 512-bit hex string prefixed with 0x"


def test_root_independent_of_json_key_order():
    policy, policy_path = _load_example_policy()
    builder = _discover_builder()

    # Reorder keys in a structured way to exercise canonicalization:
    reordered = {}
    # Move metadata-ish fields (if present) to front to change insertion order.
    for k in ("meta", "version", "notes"):
        if k in policy:
            reordered[k] = policy[k]
    # Put enabled/weights/algs in a different order if present.
    for k in ("weights", "enabled", "algs", "thresholds"):
        if k in policy:
            if isinstance(policy[k], dict):
                # reverse insertion order of inner dict
                inner = list(policy[k].items())[::-1]
                reordered[k] = {ik: iv for ik, iv in inner}
            else:
                reordered[k] = policy[k]
    # Append any remaining keys
    for k, v in policy.items():
        if k not in reordered:
            reordered[k] = v

    # Compute roots. If dict-call fails, fall back to path-call (should be same).
    try:
        r_orig = builder(policy)
        r_reo = builder(reordered)
    except RuntimeError:
        r_orig = builder(policy_path)
        r_reo = builder(policy_path)

    assert r_orig == r_reo, "root must not depend on JSON key order"


def test_root_changes_on_semantic_change():
    policy, policy_path = _load_example_policy()
    builder = _discover_builder()

    # Baseline root
    try:
        r_base = builder(policy)
    except RuntimeError:
        r_base = builder(policy_path)

    # Mutate a semantically relevant field.
    mutated = copy.deepcopy(policy)
    changed = False

    # Prefer adjusting a weight if present.
    if isinstance(mutated.get("weights"), dict) and mutated["weights"]:
        k0 = next(iter(mutated["weights"]))
        # flip a single weight by +1 (modest change)
        try:
            mutated["weights"][k0] = int(mutated["weights"][k0]) + 1
            changed = True
        except Exception:
            pass

    # If no weights, try flipping an enable flag.
    if not changed and isinstance(mutated.get("enabled"), dict) and mutated["enabled"]:
        k0 = next(iter(mutated["enabled"]))
        if isinstance(mutated["enabled"][k0], bool):
            mutated["enabled"][k0] = not mutated["enabled"][k0]
            changed = True

    # If still not changed, append a deprecation marker (likely part of Merkle data).
    if not changed and isinstance(mutated.get("deprecated"), list):
        mutated["deprecated"].append("unit-test-added")
        changed = True

    if not changed:
        pytest.skip("example policy has no recognized semantic knobs to mutate")

    try:
        r_mut = builder(mutated)
    except RuntimeError:
        # If builder only accepts path, write a temp policy file to compare.
        tmp = policy_path + ".tmp-test.json"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(mutated, f, separators=(",", ":"), sort_keys=False)
        try:
            r_mut = builder(tmp)
        finally:
            try:
                os.remove(tmp)
            except OSError:
                pass

    assert r_mut != r_base, "changing a semantic parameter must change the Merkle root"


def test_cli_script_outputs_same_root(monkeypatch):
    """
    If the CLI module pq.cli.pq_alg_policy_root exists, exercise it in-process
    (import and call its main-like function) and ensure it matches the library result.
    """
    policy, policy_path = _load_example_policy()
    builder = _discover_builder()

    try:
        cli_mod = import_module("pq.cli.pq_alg_policy_root")
    except Exception:
        pytest.xfail("CLI pq.cli.pq_alg_policy_root not present")

    # Compute via library
    try:
        r_lib = builder(policy)
    except RuntimeError:
        r_lib = builder(policy_path)

    # Try to call a function that returns the hex root without exiting the interpreter.
    # Accept signatures:
    #   - compute(path) -> str
    #   - main(argv) -> str or prints to stdout
    root_cli: str | None = None

    if hasattr(cli_mod, "compute") and callable(getattr(cli_mod, "compute")):
        root_cli = cli_mod.compute(policy_path)  # type: ignore[attr-defined]
    elif hasattr(cli_mod, "main") and callable(getattr(cli_mod, "main")):
        # Capture stdout
        import io
        import sys

        buf = io.StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            cli_mod.main([policy_path])  # type: ignore[attr-defined]
        finally:
            sys.stdout = old
        out = buf.getvalue().strip().splitlines()[-1].strip()
        # Accept either raw hex or "root=0x..."
        root_cli = out.split("=", 1)[-1].strip() if "=" in out else out

    if root_cli is None:
        pytest.xfail("CLI module present but no callable interface recognized")

    assert r_lib == root_cli, "CLI output root must match library-computed root"
    assert _is_hex512(root_cli), "CLI root must be a 512-bit hex string"
