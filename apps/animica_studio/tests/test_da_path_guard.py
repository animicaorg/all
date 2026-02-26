from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from animica_studio.services.da_path_guard import NODE_PATH_UI_ERROR, assert_host_writable_path, is_node_path


def test_is_node_path_detects_data_prefix() -> None:
    assert is_node_path('/data/chain-1/da') is True


def test_is_node_path_detects_rpc_reported_node_path() -> None:
    assert is_node_path('/mnt/node/da/pending', extra_node_paths={'/mnt/node/da'}) is True


def test_assert_host_writable_path_rejects_node_path() -> None:
    with pytest.raises(ValueError, match='Studio needs a host path'):
        assert_host_writable_path('/data/chain-1/da')


def test_assert_host_writable_path_accepts_home_path() -> None:
    out = assert_host_writable_path('~/.animica/da_contrib')
    assert str(out).endswith('/.animica/da_contrib')
    assert NODE_PATH_UI_ERROR
