import os
import itertools
import pytest

# The intrinsic-gas helpers live here
from execution.gas.intrinsic import calc_intrinsic_gas  # type: ignore
from execution.gas.meter import GasMeter  # type: ignore
from execution.errors import OOG  # type: ignore


@pytest.mark.parametrize("payload", [b"", b"\x00" * 1, b"\x00" * 32, b"\x11" * 1, b"\x11" * 32])
def test_transfer_intrinsic_monotonic(payload: bytes) -> None:
    """
    Sanity: intrinsic gas for a transfer is >= empty-payload base and grows with payload length.
    """
    base = calc_intrinsic_gas(kind="transfer", payload=b"", access_list=[])
    g = calc_intrinsic_gas(kind="transfer", payload=payload, access_list=[])
    assert g >= base
    assert g - base >= 0
    if payload:
        # non-zero additional cost whenever we have any payload bytes
        assert g > base


def test_zero_vs_nonzero_byte_cost_ratio() -> None:
    """
    Canonical rule (Ethereum-like): zero-bytes are cheaper than non-zero bytes.
    We don't rely on specific constants here; we assert the *per-byte* cost for
    non-zero exceeds zero, and that costs scale linearly with length.
    """
    base = calc_intrinsic_gas(kind="transfer", payload=b"", access_list=[])

    z8 = calc_intrinsic_gas(kind="transfer", payload=b"\x00" * 8, access_list=[])
    z32 = calc_intrinsic_gas(kind="transfer", payload=b"\x00" * 32, access_list=[])

    nz8 = calc_intrinsic_gas(kind="transfer", payload=b"\x11" * 8, access_list=[])
    nz32 = calc_intrinsic_gas(kind="transfer", payload=b"\x11" * 32, access_list=[])

    # Linear scaling with length
    assert (z32 - base) * 1 == (z8 - base) * 4
    assert (nz32 - base) * 1 == (nz8 - base) * 4

    # Non-zero bytes cost more per byte than zero bytes
    assert (nz8 - base) > (z8 - base)
    assert (nz32 - base) > (z32 - base)


def test_access_list_additivity() -> None:
    """
    Access-list cost should be additive across addresses and keys.
    We'll check:
      - one address, 0 keys
      - one address, N keys
      - two addresses (combine)
    and ensure: cost(addr A + addr B) == cost(A) + cost(B) - base
    """
    base = calc_intrinsic_gas(kind="transfer", payload=b"", access_list=[])

    def g(al):
        return calc_intrinsic_gas(kind="transfer", payload=b"", access_list=al)

    addr_a = (b"\xaa" * 32, [])
    addr_b = (b"\xbb" * 32, [])

    g_a = g([addr_a])
    g_b = g([addr_b])
    g_ab = g([addr_a, addr_b])

    # Adding second address should increase cost and be additive (minus single base)
    assert g_a > base
    assert g_b > base
    assert g_ab >= g_a
    assert g_ab >= g_b
    # Additivity: incremental cost(A) + incremental cost(B) == incremental cost(A+B)
    assert (g_a - base) + (g_b - base) == (g_ab - base)

    # Now include storage keys and check linearity by key count
    addr_a_2k = (b"\xaa" * 32, [b"\x01" * 32, b"\x02" * 32])
    g_a2 = g([addr_a_2k])
    # Two keys must cost more than zero keys
    assert g_a2 > g_a

    # Doubling the number of keys doubles the incremental key cost (same address)
    addr_a_4k = (b"\xaa" * 32, [b"\x01" * 32, b"\x02" * 32, b"\x03" * 32, b"\x04" * 32])
    g_a4 = g([addr_a_4k])
    # Remove the per-address base from both; compare key-only deltas
    delta_keys_2 = g_a2 - g_a
    delta_keys_4 = g_a4 - g_a2
    assert delta_keys_2 > 0
    assert delta_keys_4 == delta_keys_2  # adding +2 keys again should add the same amount


@pytest.mark.parametrize(
    "payload_len,nonzero",
    [
        (0, False),
        (0, True),
        (1, False),
        (1, True),
        (32, False),
        (32, True),
    ],
)
def test_oog_boundary_with_gasmeter(payload_len: int, nonzero: bool) -> None:
    """
    Boundary check: a GasMeter with (intrinsic - 1) should raise OOG when debiting
    intrinsic; a GasMeter with exactly intrinsic should not raise.
    """
    payload = (b"\x11" if nonzero else b"\x00") * payload_len
    intrinsic = calc_intrinsic_gas(kind="transfer", payload=payload, access_list=[])

    # Just below intrinsic → OOG
    gm = GasMeter(gas_limit=intrinsic - 1)
    with pytest.raises(OOG):
        gm.debit(intrinsic)

    # Exactly intrinsic → ok
    gm_ok = GasMeter(gas_limit=intrinsic)
    gm_ok.debit(intrinsic)  # should not raise


def test_intrinsic_never_decreases_with_access_list_and_payload() -> None:
    """
    For any combination of payload and access list, intrinsic gas must be
    >= the empty/none case.
    """
    base = calc_intrinsic_gas(kind="transfer", payload=b"", access_list=[])
    payloads = [b"", b"\x00" * 8, b"\x11" * 8, b"\x00" * 32, b"\xff" * 32]
    access_lists = [
        [],
        [(b"\xaa" * 32, [])],
        [(b"\xaa" * 32, [b"\x01" * 32])],
        [(b"\xaa" * 32, [b"\x01" * 32, b"\x02" * 32]), (b"\xbb" * 32, [])],
    ]
    for pl, al in itertools.product(payloads, access_lists):
        g = calc_intrinsic_gas(kind="transfer", payload=pl, access_list=al)
        assert g >= base, (len(pl), al)


def test_deploy_and_call_have_at_least_transfer_base() -> None:
    """
    Basic shape: other kinds shouldn't be *cheaper* than transfer with empty payload.
    (Exact constants are implementation-defined; we check ordering only.)
    """
    base_transfer = calc_intrinsic_gas(kind="transfer", payload=b"", access_list=[])
    base_deploy = calc_intrinsic_gas(kind="deploy", payload=b"", access_list=[])
    base_call = calc_intrinsic_gas(kind="call", payload=b"", access_list=[])

    assert base_deploy >= base_transfer
    assert base_call >= base_transfer
