from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

from vm_py.cli.compile import compile_via_lower_pipeline
from vm_py.compiler.lowering_adapter import lower_source_with_compat


def test_lowering_adapter_supports_ast_only_signature() -> None:
    seen: dict[str, object] = {}

    def lower_to_ir(tree):  # noqa: ANN001
        seen["ast"] = isinstance(tree, ast.Module)
        return {"ok": True}

    module = SimpleNamespace(lower_to_ir=lower_to_ir)
    result, meta = lower_source_with_compat(
        module,
        source="def get():\n    return 1\n",
        filename="contract.py",
        candidate_order=[("lower_to_ir", "ast")],
    )
    assert result == {"ok": True}
    assert seen.get("ast") is True
    assert meta.helper_name == "lower_to_ir"


def test_lowering_adapter_supports_ast_with_name_signature() -> None:
    seen: dict[str, object] = {}

    def lower_to_ir(tree, name):  # noqa: ANN001
        seen["ast"] = isinstance(tree, ast.Module)
        seen["name"] = name
        return {"ok": True}

    module = SimpleNamespace(lower_to_ir=lower_to_ir)
    result, _meta = lower_source_with_compat(
        module,
        source="def get():\n    return 1\n",
        filename="counter.py",
        candidate_order=[("lower_to_ir", "ast")],
    )
    assert result == {"ok": True}
    assert seen.get("ast") is True
    assert seen.get("name") == "counter.py"


def test_lowering_adapter_supports_ast_with_keyword_filename() -> None:
    seen: dict[str, object] = {}

    def lower_to_ir(tree, *, filename):  # noqa: ANN001
        seen["ast"] = isinstance(tree, ast.Module)
        seen["filename"] = filename
        return {"ok": True}

    module = SimpleNamespace(lower_to_ir=lower_to_ir)
    result, _meta = lower_source_with_compat(
        module,
        source="def get():\n    return 1\n",
        filename="counter.py",
        candidate_order=[("lower_to_ir", "ast")],
    )
    assert result == {"ok": True}
    assert seen.get("ast") is True
    assert seen.get("filename") == "counter.py"


def test_lowering_adapter_supports_source_signature() -> None:
    seen: dict[str, object] = {}

    def lower_to_ir(source, filename=None):  # noqa: ANN001
        seen["source"] = source
        seen["filename"] = filename
        return {"ok": True}

    module = SimpleNamespace(lower_to_ir=lower_to_ir)
    result, _meta = lower_source_with_compat(
        module,
        source="def get():\n    return 1\n",
        filename="counter.py",
        candidate_order=[("lower_to_ir", "source")],
    )
    assert result == {"ok": True}
    assert isinstance(seen.get("source"), str)
    assert "def get" in str(seen.get("source"))
    assert seen.get("filename") == "counter.py"


def test_compile_lower_pipeline_uses_actual_lower_to_ir_signature() -> None:
    ir_bytes, meta = compile_via_lower_pipeline(
        "def get():\n    return 1\n",
        filename="actual.py",
    )
    assert isinstance(ir_bytes, bytes) and len(ir_bytes) > 0
    lowering = meta.get("lowering")
    assert isinstance(lowering, dict)
    assert lowering.get("helper") == "lower_to_ir"
    assert "filename" in str(lowering.get("signature"))


@pytest.mark.parametrize(
    "relative_path",
    [
        "contracts/packages/counter/contract.py",
        "contracts/templates/counter/contract.py",
        "vm_py/examples/counter/contract.py",
        "vm_py/examples/min_counter/contract.py",
    ],
)
def test_compile_source_to_ir_accepts_repo_counter_examples(relative_path: str) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    source_path = repo_root / relative_path
    assert source_path.exists(), f"missing test source: {source_path}"

    source_text = source_path.read_text(encoding="utf-8")
    ir_bytes, meta = compile_via_lower_pipeline(source_text, filename=str(source_path))
    assert isinstance(ir_bytes, bytes) and len(ir_bytes) > 0
    assert isinstance(meta, dict)

