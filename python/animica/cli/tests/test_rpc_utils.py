from __future__ import annotations

import pytest

from animica.cli.rpc_utils import is_local_rpc_url


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("http://127.0.0.1:8545/rpc", True),
        ("http://localhost:8545/rpc", True),
        ("http://[::1]:8545/rpc", True),
        ("http://0.0.0.0:8545/rpc", True),
        ("127.0.0.1:8545/rpc", True),
        ("https://rpc.animica.org/rpc", False),
        ("http://192.168.1.10:8545/rpc", False),
    ],
)
def test_is_local_rpc_url(url: str, expected: bool) -> None:
    assert is_local_rpc_url(url) is expected
