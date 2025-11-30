import inspect
import sys
import types

# The module under test
import omni_sdk.contracts.codegen as cg
import pytest


def _pick_generator():
    """
    Find a reasonable generator function in omni_sdk.contracts.codegen.
    We try a few common names to stay resilient to refactors.
    """
    candidates = [
        "generate_python_client",
        "emit_python_client",
        "emit_python",
        "codegen_python",
        "codegen",
        "generate",
    ]
    for name in candidates:
        fn = getattr(cg, name, None)
        if callable(fn):
            return fn
    return None


def _invoke_generator(gen_fn, abi, class_name="Counter"):
    """
    Call the generator with best-effort argument matching.
    It should return Python source code as a string.
    """
    # Try (abi, class_name=...)
    try:
        return gen_fn(abi, class_name=class_name)  # type: ignore[misc,call-arg]
    except TypeError:
        pass
    # Try (abi, class_name)
    try:
        return gen_fn(abi, class_name)  # type: ignore[misc,call-arg]
    except TypeError:
        pass
    # Try (abi) only
    return gen_fn(abi)  # type: ignore[misc,call-arg]


def _install_client_stub():
    """
    Insert a stub ContractClient at import path `omni_sdk.contracts.client`
    so the generated code can import it without talking to a real node.
    """
    pkg = sys.modules.setdefault("omni_sdk", types.ModuleType("omni_sdk"))
    sub = sys.modules.setdefault(
        "omni_sdk.contracts", types.ModuleType("omni_sdk.contracts")
    )

    client_mod = types.ModuleType("omni_sdk.contracts.client")

    class DummyTx:
        def __init__(self):
            self.sign_bytes = b"\x00"

        def attach_signature(self, **kwargs):
            return None

    class ContractClient:
        def __init__(self, rpc, address, abi, chain_id=1):
            self.rpc = rpc
            self.address = address
            self.abi = abi
            self.chain_id = chain_id

        # common read path
        def read(self, fn_name, *args, **kwargs):
            if fn_name == "get":
                return 42
            return None

        # some generators may use "call" for read
        def call(self, fn_name, *args, **kwargs):
            return self.read(fn_name, *args, **kwargs)

        # write path — return a dummy tx object
        def build_tx(self, fn_name, *args, **kwargs):
            return DummyTx()

        # estimate path (optional)
        def estimate_gas(self, fn_name, *args, **kwargs):
            return 123456

    client_mod.ContractClient = ContractClient  # type: ignore[attr-defined]
    sys.modules["omni_sdk.contracts.client"] = client_mod


def test_codegen_emits_valid_source_and_compiles():
    gen = _pick_generator()
    if gen is None:
        pytest.skip(
            "no suitable generator function found in omni_sdk.contracts.codegen"
        )

    # Minimal ABI for Counter: get() -> uint64 (view), inc() (nonpayable)
    abi = [
        {
            "type": "function",
            "name": "get",
            "inputs": [],
            "outputs": [{"name": "", "type": "uint64"}],
            "stateMutability": "view",
        },
        {
            "type": "function",
            "name": "inc",
            "inputs": [],
            "outputs": [],
            "stateMutability": "nonpayable",
        },
    ]

    src = _invoke_generator(gen, abi, class_name="Counter")
    assert isinstance(src, str) and len(src) > 0
    # Basic structure checks
    assert "class Counter" in src
    assert "def get(" in src
    assert "def inc(" in src

    # Make sure generated code can import ContractClient without real SDK runtime
    _install_client_stub()

    # Compile the generated module in an isolated namespace
    ns: dict = {}
    compiled = compile(src, filename="<generated Counter>", mode="exec")
    exec(compiled, ns, ns)

    # Find the class
    Counter = ns.get("Counter")
    assert inspect.isclass(Counter), "Generated class 'Counter' not found"

    # Instantiate with a fake RPC (could be anything, the stub ignores it)
    c = Counter(object(), "anim1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq", chain_id=1)

    # Call view: get() should proxy to ContractClient.read/call and return 42
    assert hasattr(c, "get"), "Generated class missing method 'get'"
    assert c.get() == 42

    # Call nonpayable: inc() should return a tx-like object (or None, but must not raise)
    assert hasattr(c, "inc"), "Generated class missing method 'inc'"
    tx = c.inc(sender="anim1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq")
    # Accept either a tx-like object with .sign_bytes or any non-raising return
    assert tx is None or hasattr(tx, "sign_bytes")
