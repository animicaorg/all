"""
Test rich list RPC methods (state.getRichList and state.getTotalSupply).
"""
import pytest


def test_state_get_rich_list_basic(tmp_path, genesis_with_funds):
    """
    Test that state.getRichList returns accounts sorted by balance.
    """
    from core.db.kv import SqliteKV
    from core.db.state_db import StateDB
    from rpc import deps
    from rpc.methods.state import state_get_rich_list

    # Create a state DB with some accounts
    kv = SqliteKV(str(tmp_path / "test.db"))
    state_db = StateDB(kv)

    # Add some accounts with different balances
    addr1 = b"\x01" * 32
    addr2 = b"\x02" * 32
    addr3 = b"\x03" * 32

    state_db.set_balance(addr1, 1000000000)  # 1 ANM
    state_db.set_balance(addr2, 5000000000)  # 5 ANM
    state_db.set_balance(addr3, 2000000000)  # 2 ANM

    # Set up context
    ctx = deps.RpcContext()
    ctx.state_db = state_db
    deps._set_ctx(ctx)

    try:
        # Call getRichList
        result = state_get_rich_list(limit=10, offset=0)

        # Verify structure
        assert "height" in result
        assert "totalAddresses" in result
        assert "items" in result
        assert result["totalAddresses"] == 3

        # Verify sorting (highest balance first)
        items = result["items"]
        assert len(items) == 3
        assert items[0]["rank"] == 1
        assert items[1]["rank"] == 2
        assert items[2]["rank"] == 3

        # Check balances are in descending order
        balance1 = int(items[0]["balance"], 16)
        balance2 = int(items[1]["balance"], 16)
        balance3 = int(items[2]["balance"], 16)
        assert balance1 >= balance2 >= balance3
        assert balance1 == 5000000000  # addr2
        assert balance2 == 2000000000  # addr3
        assert balance3 == 1000000000  # addr1

    finally:
        deps._clear_ctx()
        kv.close()


def test_state_get_rich_list_pagination(tmp_path):
    """
    Test that pagination works correctly.
    """
    from core.db.kv import SqliteKV
    from core.db.state_db import StateDB
    from rpc import deps
    from rpc.methods.state import state_get_rich_list

    kv = SqliteKV(str(tmp_path / "test.db"))
    state_db = StateDB(kv)

    # Add 10 accounts
    for i in range(10):
        addr = bytes([i]) * 32
        balance = (10 - i) * 1000000000  # Descending balances
        state_db.set_balance(addr, balance)

    ctx = deps.RpcContext()
    ctx.state_db = state_db
    deps._set_ctx(ctx)

    try:
        # Get first page
        result1 = state_get_rich_list(limit=3, offset=0)
        assert result1["totalAddresses"] == 10
        assert len(result1["items"]) == 3
        assert result1["items"][0]["rank"] == 1
        assert result1["items"][2]["rank"] == 3

        # Get second page
        result2 = state_get_rich_list(limit=3, offset=3)
        assert result2["totalAddresses"] == 10
        assert len(result2["items"]) == 3
        assert result2["items"][0]["rank"] == 4
        assert result2["items"][2]["rank"] == 6

        # Get last page (partial)
        result3 = state_get_rich_list(limit=3, offset=9)
        assert result3["totalAddresses"] == 10
        assert len(result3["items"]) == 1
        assert result3["items"][0]["rank"] == 10

    finally:
        deps._clear_ctx()
        kv.close()


def test_state_get_total_supply(tmp_path):
    """
    Test that state.getTotalSupply returns correct sum of all balances.
    """
    from core.db.kv import SqliteKV
    from core.db.state_db import StateDB
    from rpc import deps
    from rpc.methods.state import state_get_total_supply

    kv = SqliteKV(str(tmp_path / "test.db"))
    state_db = StateDB(kv)

    # Add accounts
    addr1 = b"\x01" * 32
    addr2 = b"\x02" * 32
    addr3 = b"\x03" * 32

    state_db.set_balance(addr1, 1000000000)
    state_db.set_balance(addr2, 2000000000)
    state_db.set_balance(addr3, 3000000000)

    ctx = deps.RpcContext()
    ctx.state_db = state_db
    deps._set_ctx(ctx)

    try:
        result = state_get_total_supply()

        assert "height" in result
        assert "totalSupply" in result
        assert "addressCount" in result

        # Check total supply
        total_supply = int(result["totalSupply"], 16)
        assert total_supply == 6000000000  # Sum of all balances

        # Check address count
        assert result["addressCount"] == 3

    finally:
        deps._clear_ctx()
        kv.close()


def test_state_get_rich_list_filters_zero_balance(tmp_path):
    """
    Test that accounts with zero balance are not included in rich list.
    """
    from core.db.kv import SqliteKV
    from core.db.state_db import StateDB
    from rpc import deps
    from rpc.methods.state import state_get_rich_list

    kv = SqliteKV(str(tmp_path / "test.db"))
    state_db = StateDB(kv)

    # Add accounts with mixed balances
    addr1 = b"\x01" * 32
    addr2 = b"\x02" * 32
    addr3 = b"\x03" * 32
    addr4 = b"\x04" * 32

    state_db.set_balance(addr1, 1000000000)
    state_db.set_balance(addr2, 0)  # Zero balance
    state_db.set_balance(addr3, 2000000000)
    state_db.set_balance(addr4, 0)  # Zero balance

    ctx = deps.RpcContext()
    ctx.state_db = state_db
    deps._set_ctx(ctx)

    try:
        result = state_get_rich_list(limit=10, offset=0)

        # Should only include non-zero balances
        assert result["totalAddresses"] == 2
        assert len(result["items"]) == 2

    finally:
        deps._clear_ctx()
        kv.close()
