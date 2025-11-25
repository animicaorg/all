import importlib
import types
import pytest


def _load_fn():
    """
    Be resilient to slight naming differences: try common function names.
    Expected signature:
        fn(chain_id: int, height: int, tx_hash: bytes|str, caller: bytes|str, payload: bytes|str) -> bytes|str
    """
    mod: types.ModuleType = importlib.import_module("capabilities.jobs.id")
    for name in ("derive_task_id", "task_id", "compute_task_id"):
        fn = getattr(mod, name, None)
        if callable(fn):
            return fn
    raise RuntimeError("No task-id function found in capabilities.jobs.id")


task_id_fn = _load_fn()


def test_same_inputs_produce_same_id():
    chain_id = 1
    height = 123456
    tx_hash = bytes.fromhex("11" * 32)  # 32 bytes
    caller = bytes.fromhex("22" * 32)   # 32 bytes (address payload)
    payload = b'{"model":"tiny","prompt":"hello"}'

    tid1 = task_id_fn(chain_id, height, tx_hash, caller, payload)
    tid2 = task_id_fn(chain_id, height, tx_hash, caller, payload)

    assert tid1 == tid2, "Task ID must be deterministic for identical inputs"


@pytest.mark.parametrize("delta", [1, 7, 12345])
def test_different_height_produces_different_id(delta: int):
    chain_id = 1
    base_height = 1000
    tx_hash = bytes.fromhex("aa" * 32)
    caller = bytes.fromhex("bb" * 32)
    payload = b"\x01\x02\x03"

    tid_a = task_id_fn(chain_id, base_height, tx_hash, caller, payload)
    tid_b = task_id_fn(chain_id, base_height + delta, tx_hash, caller, payload)

    assert tid_a != tid_b, "Changing height must change the derived task ID"


def test_other_fields_affect_id():
    chain_id = 9
    height = 42
    tx_hash = bytes.fromhex("de" * 32)
    caller = bytes.fromhex("ad" * 32)
    payload = b"A"

    tid_base = task_id_fn(chain_id, height, tx_hash, caller, payload)

    # Change tx_hash
    tid_tx = task_id_fn(chain_id, height, bytes.fromhex("be" * 32), caller, payload)
    assert tid_tx != tid_base, "Changing tx_hash must change the task ID"

    # Change caller
    tid_caller = task_id_fn(chain_id, height, tx_hash, bytes.fromhex("ef" * 32), payload)
    assert tid_caller != tid_base, "Changing caller must change the task ID"

    # Change payload
    tid_payload = task_id_fn(chain_id, height, tx_hash, caller, b"B")
    assert tid_payload != tid_base, "Changing payload must change the task ID"
