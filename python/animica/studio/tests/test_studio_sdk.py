"""Tests for the Animica Studio SDK.

Pure-unit tests (image refs, serialization) plus a couple of real local-mode
execution tests that actually spawn the sandbox subprocess.

Run:  PYTHONPATH=python pytest python/animica/studio/tests -q
"""

from __future__ import annotations

import pytest

import animica.studio as studio
from animica.studio import serialize
from animica.studio.errors import ExecutionError


# ---- image identity -----------------------------------------------------------


def test_image_ref_is_deterministic_and_content_addressed():
    a = studio.Image.debian_slim().pip_install("numpy", "scipy")
    b = studio.Image.debian_slim().pip_install("numpy", "scipy")
    c = studio.Image.debian_slim().pip_install("numpy")
    assert a.ref == b.ref
    assert a.ref != c.ref
    assert a.ref.startswith("studio-img:")


def test_image_env_is_order_independent():
    a = studio.Image.debian_slim().env(A="1", B="2")
    b = studio.Image.debian_slim().env(B="2", A="1")
    assert a.ref == b.ref


# ---- serialization ------------------------------------------------------------


def _top_level(x):  # module-level so ref mode can address it
    return x * 2


def test_pack_call_ref_mode():
    spec = serialize.pack_call(_top_level, (21,), {}, mode="ref")
    assert spec["mode"] == "ref"
    assert spec["entrypoint"].endswith(":_top_level")
    assert spec["args"] == [21]
    # round-trips through dumps/loads
    assert serialize.loads(serialize.dumps(spec))["args"] == [21]


def test_pack_call_ref_rejects_unserializable_args():
    with pytest.raises(Exception):
        serialize.pack_call(_top_level, (object(),), {}, mode="ref")


def test_result_pack_unpack_json_and_fallback():
    assert serialize.unpack_result(serialize.pack_result({"a": [1, 2]})) == {"a": [1, 2]}
    assert serialize.unpack_result(serialize.pack_result(42)) == 42


# ---- local execution (real subprocess) ---------------------------------------


app = studio.App("studio_tests", config=studio.StudioConfig.from_env(mode="local"))


@app.function()
def double(x: int) -> int:
    return x * 2


@app.function()
def boom():
    raise ValueError("kaboom")


def test_function_not_directly_callable():
    with pytest.raises(TypeError):
        double(3)  # must use .remote()/.local()


def test_local_returns_value_in_process():
    assert double.local(5) == 10


def test_remote_local_mode_runs_in_sandbox():
    assert double.remote(5) == 10


def test_map_local_mode():
    assert double.map([1, 2, 3]) == [2, 4, 6]


def test_remote_error_surfaces_traceback():
    with pytest.raises(ExecutionError) as ei:
        boom.remote()
    assert "kaboom" in str(ei.value)
