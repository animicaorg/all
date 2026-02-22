"""Tests for wallet models, error_format, tx_builder, and wallet_service.

No Qt / network required.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


# ---------------------------------------------------------------------------
# wallet_models
# ---------------------------------------------------------------------------


class TestFormatAmount:
    def test_one_anm(self):
        from animica_studio.models.wallet_models import format_amount

        assert format_amount(10**18) == "1 ANM"

    def test_fractional(self):
        from animica_studio.models.wallet_models import format_amount

        assert format_amount(1_500_000_000_000_000_000) == "1.5 ANM"

    def test_zero(self):
        from animica_studio.models.wallet_models import format_amount

        assert format_amount(0) == "0 ANM"

    def test_large_int(self):
        from animica_studio.models.wallet_models import format_amount

        # Should not raise even for very large ints
        result = format_amount(10**30)
        assert "ANM" in result




class TestFormatAmountCompact:
    def test_limits_fractional_digits_like_extension(self):
        from animica_studio.models.wallet_models import format_amount_compact

        assert format_amount_compact(1_234_567_890_123_456_789) == "1.234567 ANM"

    def test_trims_trailing_zeroes(self):
        from animica_studio.models.wallet_models import format_amount_compact

        assert format_amount_compact(1_500_000_000_000_000_000) == "1.5 ANM"

    def test_zero(self):
        from animica_studio.models.wallet_models import format_amount_compact

        assert format_amount_compact(0) == "0 ANM"


class TestParseAmountToWei:
    def test_integer_string(self):
        from animica_studio.models.wallet_models import parse_amount_to_wei

        assert parse_amount_to_wei("1") == 10**18

    def test_fractional(self):
        from animica_studio.models.wallet_models import parse_amount_to_wei

        assert parse_amount_to_wei("1.5") == 1_500_000_000_000_000_000

    def test_with_unit_suffix(self):
        from animica_studio.models.wallet_models import parse_amount_to_wei

        assert parse_amount_to_wei("2 ANM") == 2 * 10**18

    def test_negative_raises(self):
        from animica_studio.models.wallet_models import parse_amount_to_wei

        with pytest.raises(ValueError, match="negative"):
            parse_amount_to_wei("-1")

    def test_invalid_text(self):
        from animica_studio.models.wallet_models import parse_amount_to_wei

        with pytest.raises(ValueError):
            parse_amount_to_wei("abc")


class TestShortenAddress:
    def test_short_address_unchanged(self):
        from animica_studio.models.wallet_models import shorten_address

        addr = "anim1abc"
        assert shorten_address(addr) == addr

    def test_long_address_shortened(self):
        from animica_studio.models.wallet_models import shorten_address

        addr = "anim1abcdefghijklmnopqrstuvwxyz"
        result = shorten_address(addr)
        assert "…" in result
        assert result.startswith("anim1abc")

    def test_empty(self):
        from animica_studio.models.wallet_models import shorten_address

        assert shorten_address("") == ""


class TestIsValidAddress:
    def test_valid(self):
        from animica_studio.models.wallet_models import is_valid_address

        # bech32m charset excludes b, i, o, 1 — use only valid chars
        assert is_valid_address("anim1acdefghjklmnpqrstuvwxyz0234")

    def test_too_short(self):
        from animica_studio.models.wallet_models import is_valid_address

        assert not is_valid_address("anim1ab")

    def test_wrong_prefix(self):
        from animica_studio.models.wallet_models import is_valid_address

        assert not is_valid_address("eth1abcdefghijklmno")

    def test_uppercase_invalid(self):
        from animica_studio.models.wallet_models import is_valid_address

        # bech32m is lowercase
        assert not is_valid_address("ANIM1ABCDEFGHIJKLMNO")


class TestAccountDataclass:
    def test_roundtrip(self):
        from animica_studio.models.wallet_models import Account

        acc = Account(label="Test", address="anim1testaddr12345")
        d = acc.to_dict()
        acc2 = Account.from_dict(d)
        assert acc2.id == acc.id
        assert acc2.label == acc.label
        assert acc2.address == acc.address

    def test_defaults(self):
        from animica_studio.models.wallet_models import Account

        acc = Account()
        assert acc.label == "Account"
        assert acc.id  # non-empty UUID


class TestPendingTxDataclass:
    def test_roundtrip(self):
        from animica_studio.models.wallet_models import PendingTx

        ptx = PendingTx(
            from_addr="anim1from",
            to_addr="anim1to",
            amount_wei=10**18,
            nonce=3,
            status="PENDING",
        )
        d = ptx.to_dict()
        ptx2 = PendingTx.from_dict(d)
        assert ptx2.local_id == ptx.local_id
        assert ptx2.amount_wei == ptx.amount_wei
        assert ptx2.status == "PENDING"


# ---------------------------------------------------------------------------
# error_format
# ---------------------------------------------------------------------------


class TestSafeStr:
    def test_string_passthrough(self):
        from animica_studio.services.error_format import safe_str

        assert safe_str("hello") == "hello"

    def test_dict_pretty_json(self):
        from animica_studio.services.error_format import safe_str

        result = safe_str({"a": 1})
        assert '"a"' in result
        assert "1" in result

    def test_list(self):
        from animica_studio.services.error_format import safe_str

        result = safe_str([1, 2, 3])
        assert "1" in result

    def test_exception(self):
        from animica_studio.services.error_format import safe_str

        exc = ValueError("test error")
        result = safe_str(exc)
        assert "ValueError" in result
        assert "test error" in result

    def test_never_object_object(self):
        from animica_studio.services.error_format import safe_str

        result = safe_str({"nested": {"key": "val"}})
        assert "[object Object]" not in result


class TestFormatRpcError:
    def test_string(self):
        from animica_studio.services.error_format import format_rpc_error

        assert format_rpc_error("simple error") == "simple error"

    def test_dict(self):
        from animica_studio.services.error_format import format_rpc_error

        result = format_rpc_error({"code": -32000, "message": "insufficient funds"})
        assert "-32000" in result
        assert "insufficient funds" in result

    def test_empty_string(self):
        from animica_studio.services.error_format import format_rpc_error

        result = format_rpc_error("")
        assert result  # non-empty fallback


class TestSafeJsonDumps:
    def test_large_int(self):
        from animica_studio.services.error_format import safe_json_dumps
        import json

        big = 2**256
        result = safe_json_dumps({"val": big})
        parsed = json.loads(result)
        assert parsed["val"] == big

    def test_bytes(self):
        from animica_studio.services.error_format import safe_json_dumps
        import json

        result = safe_json_dumps({"data": b"\xde\xad\xbe\xef"})
        parsed = json.loads(result)
        assert parsed["data"] == "0xdeadbeef"


# ---------------------------------------------------------------------------
# tx_builder
# ---------------------------------------------------------------------------


class TestBuildTransferTx:
    def test_valid(self):
        from animica_studio.services.tx_builder import build_transfer_tx

        tx = build_transfer_tx(
            chain_id=1,
            from_addr="anim1fromaddr12345",
            to_addr="anim1toaddr67890",
            value_wei=10**18,
            nonce=0,
        )
        assert tx["body"]["chain_id"] == 1
        assert tx["body"]["value"] == 10**18
        assert tx["sigs"] == []

    def test_missing_required_raises(self):
        from animica_studio.services.tx_builder import validate_tx_dict

        with pytest.raises(ValueError, match="missing required field"):
            validate_tx_dict({"body": {"version": 1}, "sigs": []})

    def test_negative_value_raises(self):
        from animica_studio.services.tx_builder import build_transfer_tx

        with pytest.raises(ValueError):
            build_transfer_tx(
                chain_id=1,
                from_addr="anim1from",
                to_addr="anim1to",
                value_wei=-1,
                nonce=0,
            )


class TestEncodeToCborHex:
    def test_returns_0x_prefix(self):
        from animica_studio.services.tx_builder import build_transfer_tx, encode_to_cbor_hex

        tx = build_transfer_tx(
            chain_id=1, from_addr="anim1from", to_addr="anim1to", value_wei=0, nonce=0
        )
        result = encode_to_cbor_hex(tx)
        assert result.startswith("0x")

    def test_non_empty(self):
        from animica_studio.services.tx_builder import build_transfer_tx, encode_to_cbor_hex

        tx = build_transfer_tx(
            chain_id=1, from_addr="anim1from", to_addr="anim1to", value_wei=0, nonce=0
        )
        result = encode_to_cbor_hex(tx)
        assert len(result) > 4  # more than just "0x"


# ---------------------------------------------------------------------------
# WalletService
# ---------------------------------------------------------------------------


class TestWalletServiceAccounts:
    def _make_service(self):
        from animica_studio.storage.config import Config
        from animica_studio.services.wallet_service import WalletService

        cfg = Config()
        return WalletService(cfg)

    def test_add_and_list(self):
        ws = self._make_service()
        acc = ws.add_account("Alice", "anim1aliceabcdefghij")
        accounts = ws.list_accounts()
        assert len(accounts) == 1
        assert accounts[0].label == "Alice"

    def test_duplicate_address_raises(self):
        ws = self._make_service()
        ws.add_account("Alice", "anim1aliceabcdefghij")
        with pytest.raises(ValueError, match="already tracked"):
            ws.add_account("Alice2", "anim1aliceabcdefghij")

    def test_remove_account(self):
        ws = self._make_service()
        acc = ws.add_account("Alice", "anim1aliceabcdefghij")
        assert ws.remove_account(acc.id) is True
        assert ws.list_accounts() == []

    def test_remove_nonexistent_returns_false(self):
        ws = self._make_service()
        assert ws.remove_account("nonexistent-id") is False

    def test_balances_keyed_by_address(self):
        """Each account's balance is stored independently by address."""
        from animica_studio.models.wallet_models import BalanceState
        from animica_studio.storage.config import Config
        from animica_studio.services.wallet_service import WalletService

        cfg = Config()
        ws = WalletService(cfg)
        ws.add_account("Alice", "anim1alice")
        ws.add_account("Bob", "anim1bob")

        # Simulate fetched balances
        ws._balances["anim1alice"] = BalanceState(
            address="anim1alice", balance_wei=10**18, formatted="1 ANM"
        )
        ws._balances["anim1bob"] = BalanceState(
            address="anim1bob", balance_wei=2 * 10**18, formatted="2 ANM"
        )

        alice_bal = ws.get_cached_balance("anim1alice")
        bob_bal = ws.get_cached_balance("anim1bob")

        assert alice_bal is not None
        assert bob_bal is not None
        # No aliasing — different objects, different values
        assert alice_bal.balance_wei != bob_bal.balance_wei
        assert alice_bal.formatted == "1 ANM"
        assert bob_bal.formatted == "2 ANM"




def _make_wallet_create_ops(*, options: list[str] | None = None):
    """Return a real CliOps instance backed by a pre-populated in-memory registry.

    This avoids needing the animica CLI installed in the test environment while
    still exercising the real CliOps.build logic. We use object.__new__ + manual
    attribute assignment so that CliRegistry.__init__'s file I/O is never run.
    """
    from animica_studio.services.cli_ops import CliOps
    from animica_studio.services.cli_registry import CliNode, CliRegistry

    # Bypass __init__ (which performs disk I/O) by using object.__new__ and
    # populating only the attributes that CliOps accesses: _nodes and _cli_path.
    registry = object.__new__(CliRegistry)
    registry._nodes = {
        "": CliNode(commands=["wallet"]),
        "wallet": CliNode(commands=["create", "list"]),
        "wallet create": CliNode(options=options or ["--label", "--alg"]),
    }
    registry._cli_path = ""
    return CliOps(registry)


class TestWalletServiceCreateWallet:
    def test_build_create_wallet_args(self):
        from animica_studio.storage.config import Config
        from animica_studio.services.wallet_service import WalletService

        ws = WalletService(Config())
        ops = _make_wallet_create_ops()
        args, clean_label, scheme = ws.build_create_wallet_args("My Wallet", "dilithium3")

        assert clean_label == "My Wallet"
        assert scheme == "dilithium3"
        assert args == [
            "wallet",
            "create",
            "--label",
            "My Wallet",
            "--alg",
            "dilithium3",
        ]

    def test_build_create_wallet_args_sphincs_and_fallback(self):
        from animica_studio.storage.config import Config
        from animica_studio.services.wallet_service import WalletService

        ws = WalletService(Config())
        ops = _make_wallet_create_ops()
        args, clean_label, scheme = ws.build_create_wallet_args(
            "SPX Wallet",
            "sphincs128s",
            allow_insecure_fallback=True,
        )

        assert clean_label == "SPX Wallet"
        assert scheme == "sphincs_shake_128s"
        assert args == [
            "wallet",
            "create",
            "--label",
            "SPX Wallet",
            "--alg",
            "sphincs_shake_128s",
            "--allow-insecure-fallback",
        ]


    def test_create_wallet_bad_label_raises(self):
        from animica_studio.storage.config import Config
        from animica_studio.services.wallet_service import WalletService

        ws = WalletService(Config())
        with pytest.raises(ValueError, match="Wallet label"):
            ws.build_create_wallet_args("bad/label")

    def test_create_wallet_empty_label_raises(self):
        from animica_studio.storage.config import Config
        from animica_studio.services.wallet_service import WalletService

        ws = WalletService(Config())
        with pytest.raises(ValueError, match="Wallet label"):
            ws.build_create_wallet_args("   ")

    def test_resolve_and_store_created_wallet(self):
        from animica_studio.storage.config import Config
        from animica_studio.services.wallet_service import WalletService

        ws = WalletService(Config())
        known_addresses = {"anim1existingaddr000000000000000"}
        ws._load_wallet_store_addresses = lambda: {
            "anim1existingaddr000000000000000",
            "anim1acdefghjklmnpqrstuvwxyz0234567890",
        }
        address = ws.resolve_created_wallet_address(known_addresses)
        account = ws.store_created_wallet("Wallet1", address, "dilithium3")

        assert account.label == "Wallet1"
        assert account.address == "anim1acdefghjklmnpqrstuvwxyz0234567890"
        assert account.sig_scheme == "dilithium3"


class TestWalletServiceExplorerUrls:
    def _make_service(self):
        from animica_studio.storage.config import Config
        from animica_studio.services.wallet_service import WalletService

        cfg = Config()
        return WalletService(cfg)

    def test_tx_url(self):
        ws = self._make_service()
        url = ws.explorer_url_for_tx("0xabcdef")
        assert "tx/0xabcdef" in url

    def test_address_url(self):
        ws = self._make_service()
        url = ws.explorer_url_for_address("anim1test")
        assert "address/anim1test" in url

    def test_custom_explorer_base(self):
        from animica_studio.storage.config import Config
        from animica_studio.services.wallet_service import WalletService

        cfg = Config()
        cfg.wallet_settings["explorer_base_url"] = "https://my-explorer.example.com"
        ws = WalletService(cfg)
        url = ws.explorer_url_for_tx("0xhash")
        assert url.startswith("https://my-explorer.example.com")


class TestWalletServiceFetchBalance:
    def test_success(self):
        from animica_studio.storage.config import Config
        from animica_studio.services.wallet_service import WalletService

        cfg = Config()
        ws = WalletService(cfg)

        mock_completed = MagicMock()
        mock_completed.returncode = 0
        mock_completed.stdout = '{"balance_confirmed": 5000000000000000000, "balance_confirmed_formatted": "5 ANM"}'

        with (
            patch(
                "animica_studio.services.wallet_service.resolve_animica_cli_program_and_env",
                return_value=("animica", [], {}),
            ),
            patch("animica_studio.services.wallet_service.subprocess.run", return_value=mock_completed),
        ):
            state = ws.fetch_balance("anim1test", "http://localhost:8545")

        assert state.balance_wei == 5 * 10**18
        assert state.error is None
        assert "5" in state.formatted


    def test_wallet_show_failure_falls_back_to_rpc_balance(self):
        from animica_studio.storage.config import Config
        from animica_studio.services.wallet_service import WalletService

        cfg = Config()
        ws = WalletService(cfg)

        cli_failed = MagicMock()
        cli_failed.returncode = 1
        cli_failed.stdout = ""
        cli_failed.stderr = "wallet show failed"

        rpc_client = MagicMock()
        rpc_client.get_balance.return_value = 42

        rpc_ctx = MagicMock()
        rpc_ctx.__enter__.return_value = rpc_client
        rpc_ctx.__exit__.return_value = False

        with (
            patch(
                "animica_studio.services.wallet_service.resolve_animica_cli_program_and_env",
                return_value=("animica", [], {}),
            ),
            patch("animica_studio.services.wallet_service.subprocess.run", return_value=cli_failed),
            patch("animica_studio.services.rpc_client.RpcClient", return_value=rpc_ctx),
        ):
            state = ws.fetch_balance("anim1test", "http://localhost:8545")

        assert state.error is None
        assert state.balance_wei == 42
        assert state.formatted.endswith("ANM")

    def test_network_error_returns_error_state(self):
        from animica_studio.storage.config import Config
        from animica_studio.services.wallet_service import WalletService

        cfg = Config()
        ws = WalletService(cfg)

        mock_completed = MagicMock()
        mock_completed.returncode = 1
        mock_completed.stdout = ""
        mock_completed.stderr = "Connection refused"

        with (
            patch(
                "animica_studio.services.wallet_service.resolve_animica_cli_program_and_env",
                return_value=("animica", [], {}),
            ),
            patch("animica_studio.services.wallet_service.subprocess.run", return_value=mock_completed),
        ):
            state = ws.fetch_balance("anim1test", "http://localhost:8545")

        assert state.error is not None
        assert state.balance_wei == 0

    def test_plain_text_output_parses_anm_balance(self):
        from animica_studio.storage.config import Config
        from animica_studio.services.wallet_service import WalletService

        cfg = Config()
        ws = WalletService(cfg)

        mock_completed = MagicMock()
        mock_completed.returncode = 0
        mock_completed.stdout = "Wallet: premine\nBalance: 1.25 ANM\n"
        mock_completed.stderr = ""

        with (
            patch(
                "animica_studio.services.wallet_service.resolve_animica_cli_program_and_env",
                return_value=("animica", [], {}),
            ),
            patch("animica_studio.services.wallet_service.subprocess.run", return_value=mock_completed),
        ):
            state = ws.fetch_balance("anim1test", "http://localhost:8545")

        assert state.error is None
        assert state.formatted == "1.25 ANM"


    def test_json_with_log_prefix_parses_balance(self):
        from animica_studio.storage.config import Config
        from animica_studio.services.wallet_service import WalletService

        cfg = Config()
        ws = WalletService(cfg)

        mock_completed = MagicMock()
        mock_completed.returncode = 0
        mock_completed.stdout = 'INFO connected\n{"balance": "0x3b9aca00", "balance_formatted": "1 ANM"}\n'
        mock_completed.stderr = ""

        with (
            patch(
                "animica_studio.services.wallet_service.resolve_animica_cli_program_and_env",
                return_value=("animica", [], {}),
            ),
            patch("animica_studio.services.wallet_service.subprocess.run", return_value=mock_completed),
        ):
            state = ws.fetch_balance("anim1test", "http://localhost:8545")

        assert state.error is None
        assert state.balance_wei == 1_000_000_000
        assert state.formatted == "1 ANM"

    def test_json_with_nul_bytes_parses_balance(self):
        from animica_studio.storage.config import Config
        from animica_studio.services.wallet_service import WalletService

        cfg = Config()
        ws = WalletService(cfg)

        mock_completed = MagicMock()
        mock_completed.returncode = 0
        mock_completed.stdout = '{"balance_confirmed": 2000000000, "balance_confirmed_formatted": "2 ANM"}\x00\x00'
        mock_completed.stderr = ""

        with (
            patch(
                "animica_studio.services.wallet_service.resolve_animica_cli_program_and_env",
                return_value=("animica", [], {}),
            ),
            patch("animica_studio.services.wallet_service.subprocess.run", return_value=mock_completed),
        ):
            state = ws.fetch_balance("anim1test", "http://localhost:8545")

        assert state.error is None
        assert state.balance_wei == 2_000_000_000
        assert state.formatted == "2 ANM"
    def test_error_does_not_propagate_to_other_accounts(self):
        """Per-account errors must NOT affect other accounts."""
        from animica_studio.storage.config import Config
        from animica_studio.services.wallet_service import WalletService

        cfg = Config()
        ws = WalletService(cfg)
        ws.add_account("Alice", "anim1alice")
        ws.add_account("Bob", "anim1bob")

        def fake_run(cmd, **_kwargs):
            completed = MagicMock()
            if cmd[-1] == "anim1alice":
                completed.returncode = 1
                completed.stdout = ""
                completed.stderr = "Alice's RPC failed"
                return completed
            completed.returncode = 0
            completed.stdout = '{"balance_confirmed": 3000000000000000000, "balance_confirmed_formatted": "3 ANM"}'
            completed.stderr = ""
            return completed

        with (
            patch(
                "animica_studio.services.wallet_service.resolve_animica_cli_program_and_env",
                return_value=("animica", [], {}),
            ),
            patch("animica_studio.services.wallet_service.subprocess.run", side_effect=fake_run),
        ):
            results = ws.refresh_all_balances("http://localhost:8545")

        alice_state = results.get("anim1alice")
        bob_state = results.get("anim1bob")

        assert alice_state is not None and alice_state.error is not None
        assert bob_state is not None and bob_state.error is None
        assert bob_state.balance_wei == 3 * 10**18


# ---------------------------------------------------------------------------
# Config wallet fields
# ---------------------------------------------------------------------------


class TestConfigWalletFields:
    def test_defaults(self):
        from animica_studio.storage.config import Config

        cfg = Config()
        assert cfg.accounts == []
        assert cfg.pending_txs == []
        assert cfg.wallet_settings["decimals"] == 18
        assert "explorer.animica.org" in cfg.wallet_settings["explorer_base_url"]

    def test_roundtrip(self):
        from animica_studio.storage.config import Config, _config_to_dict, _config_from_dict

        cfg = Config()
        cfg.accounts.append({"id": "x", "label": "Test", "address": "anim1x"})
        d = _config_to_dict(cfg)
        cfg2 = _config_from_dict(d)
        assert cfg2.accounts[0]["address"] == "anim1x"
        assert cfg2.wallet_settings["decimals"] == 18

class TestWalletStoreLocalFile:
    def test_load_local_wallets_from_v2_file(self, tmp_path):
        from animica_studio.services.wallet_store import WalletStore

        wallets_path = tmp_path / "wallets.json"
        wallets_path.write_text(
            """
{
  "format": "animica.wallets",
  "version": 2,
  "wallets": [
    {"label": "Alice", "address": "anim1acdefghjklmnpqrstuvwxyz023456", "alg_name": "dilithium3", "alg_id": 4097, "public_key_hex": "aa11", "created_at": "2026-01-01T00:00:00Z"},
    {"label": "Bob", "address": "anim1bcdefghjklmnpqrstuvwxyz0234567", "alg_name": "sphincs_shake_128s", "alg_id": 4098, "public_key_hex": "bb22"}
  ]
}
""".strip(),
            encoding="utf-8",
        )

        records = WalletStore().load_local_wallets(wallets_path)
        assert len(records) == 2
        assert records[0].label == "Alice"
        assert records[0].algorithm == "dilithium3"
        assert records[1].algorithm == "sphincs_shake_128s"

    def test_load_local_wallets_missing_file_returns_empty(self, tmp_path):
        from animica_studio.services.wallet_store import WalletStore

        records = WalletStore().load_local_wallets(tmp_path / "wallets.json")
        assert records == []

    def test_load_local_wallets_invalid_json_returns_empty(self, tmp_path):
        from animica_studio.services.wallet_store import WalletStore

        wallets_path = tmp_path / "wallets.json"
        wallets_path.write_text("{not valid json!!!", encoding="utf-8")
        records = WalletStore().load_local_wallets(wallets_path)
        assert records == []

    def test_load_local_wallets_unexpected_schema_returns_empty(self, tmp_path):
        """Root JSON that is a list (not a dict) should return [] without raising."""
        from animica_studio.services.wallet_store import WalletStore

        wallets_path = tmp_path / "wallets.json"
        wallets_path.write_text("[1, 2, 3]", encoding="utf-8")
        records = WalletStore().load_local_wallets(wallets_path)
        assert records == []

    def test_load_local_wallets_partial_entries(self, tmp_path):
        """Non-dict entries are skipped; dict entries without address get empty address."""
        from animica_studio.services.wallet_store import WalletStore

        wallets_path = tmp_path / "wallets.json"
        wallets_path.write_text(
            """{
  "wallets": [
    {"label": "Good", "address": "anim1goodaddr0000000"},
    "bad_entry",
    {"no_address": true}
  ]
}""",
            encoding="utf-8",
        )
        records = WalletStore().load_local_wallets(wallets_path)
        # "bad_entry" (not a dict) is skipped; the other two are returned
        assert len(records) == 2
        assert records[0].label == "Good"
        assert records[0].address == "anim1goodaddr0000000"
        # Third entry has no address; address is empty string
        assert records[1].address == ""


class TestLoadWalletsFunction:
    """Tests for the canonical load_wallets() free function."""

    def test_missing_file_returns_empty(self, tmp_path):
        from animica_studio.services.wallet_store import load_wallets

        result = load_wallets(tmp_path / "wallets.json")
        assert result == []

    def test_invalid_json_returns_empty_no_exception(self, tmp_path):
        from animica_studio.services.wallet_store import load_wallets

        p = tmp_path / "wallets.json"
        p.write_text("{ broken json", encoding="utf-8")
        result = load_wallets(p)
        assert result == []

    def test_valid_json_returns_wallets(self, tmp_path):
        from animica_studio.services.wallet_store import load_wallets

        p = tmp_path / "wallets.json"
        p.write_text(
            '{"wallets": [{"label": "Test", "address": "anim1testaddr000000000"}]}',
            encoding="utf-8",
        )
        result = load_wallets(p)
        assert len(result) == 1
        assert result[0].label == "Test"
        assert result[0].address == "anim1testaddr000000000"


class TestWalletStoreImportSmoke:
    """wallet_store must be importable even when animica.wallet.serialization is absent."""

    def test_import_wallet_store(self):
        import importlib

        mod = importlib.import_module("animica_studio.services.wallet_store")
        assert mod is not None
        assert hasattr(mod, "WalletStore")
        assert hasattr(mod, "WalletRecord")
        assert hasattr(mod, "load_wallets")

    def test_wallet_page_importable(self):
        pytest.importorskip("PySide6.QtWidgets", reason="PySide6 not available in this environment", exc_type=ImportError)
        import importlib

        mod = importlib.import_module("animica_studio.ui.pages.wallet_page")
        assert mod is not None
        assert hasattr(mod, "WalletPage")

    def test_app_module_importable(self):
        pytest.importorskip("PySide6.QtWidgets", reason="PySide6 not available in this environment", exc_type=ImportError)
        import importlib

        mod = importlib.import_module("animica_studio.app")
        assert mod is not None
        assert hasattr(mod, "main")


class TestProfileHelpers:
    def test_is_local_rpc_url_hosts(self):
        from animica_studio.services.profile_helpers import is_local_rpc_url

        assert is_local_rpc_url("http://127.0.0.1:8545/rpc") is True
        assert is_local_rpc_url("http://localhost:8545/rpc") is True
        assert is_local_rpc_url("http://0.0.0.0:8545/rpc") is True
        assert is_local_rpc_url("https://mainnet.animica.org/rpc") is False


class TestWalletServiceSchemeLabel:
    def test_unknown_scheme_friendly_label(self):
        from animica_studio.services.wallet_service import WalletService

        assert WalletService.scheme_label("unknown") == "Unknown"
        assert WalletService.scheme_label("") == "Unknown"


class TestWalletServiceSendCli:
    def test_build_and_send_uses_cli_tx_send(self):
        from animica_studio.storage.config import Config
        from animica_studio.services.wallet_service import WalletService

        cfg = Config()
        ws = WalletService(cfg)

        completed = MagicMock()
        completed.returncode = 0
        completed.stdout = "submitted tx 0x" + "a" * 64
        completed.stderr = ""

        with patch("animica_studio.services.wallet_service.resolve_animica_cli_program_and_env", return_value=("animica", [], {})):
            with patch("subprocess.run", return_value=completed) as mock_run:
                ptx = ws.build_and_send(
                    rpc_url="http://localhost:8545/rpc",
                    chain_id=1,
                    from_addr="anim1from",
                    to_addr="anim1to",
                    amount_wei=1_500_000_000_000_000_000,
                )

        assert ptx.status == "PENDING"
        assert ptx.tx_hash == "0x" + "a" * 64
        cmd = mock_run.call_args.args[0]
        assert cmd[:3] == ["animica", "tx", "send"]
        assert "--from" in cmd and "anim1from" in cmd
        assert "--to" in cmd and "anim1to" in cmd
        assert "--value" in cmd and "1.5" in cmd


# ---------------------------------------------------------------------------
# New tests required by problem statement
# ---------------------------------------------------------------------------


class TestBalanceHexParsing:
    """Balance parsing: hex string → int → ANM string formatting."""

    def test_hex_string_to_int(self):
        from animica_studio.models.rpc_models import parse_hex_quantity

        # "0x11fc..." (a real-world-style hex balance value) must parse to the correct int
        assert parse_hex_quantity("0x1", "balance") == 1
        assert parse_hex_quantity("0xde0b6b3a7640000", "balance") == 10 ** 18

    def test_int_to_anm_string(self):
        from animica_studio.models.wallet_models import format_amount

        # 1 ANM = 10^18 base units
        assert format_amount(10 ** 18) == "1 ANM"
        assert format_amount(0) == "0 ANM"

    def test_hex_balance_full_pipeline(self):
        """End-to-end: hex RPC response → integer → human-readable ANM."""
        from animica_studio.models.rpc_models import parse_hex_quantity
        from animica_studio.models.wallet_models import format_amount

        raw_hex = "0xde0b6b3a7640000"  # 1 ANM in wei
        qty = parse_hex_quantity(raw_hex, "balance")
        assert qty == 10 ** 18
        display = format_amount(qty)
        assert display == "1 ANM"

    def test_hex_large_balance(self):
        """A realistic large balance parses and formats without overflow."""
        from animica_studio.models.rpc_models import parse_hex_quantity
        from animica_studio.models.wallet_models import format_amount

        raw_hex = "0x11fc3e7aba5d3f1a"  # arbitrary large value
        qty = parse_hex_quantity(raw_hex, "balance")
        assert qty > 0
        display = format_amount(qty)
        assert "ANM" in display


class TestRpcSendRawTxGuard:
    """RPC param-shape guard: sending dict to tx_sendRawTransaction raises a local error."""

    def test_dict_param_raises_type_error(self):
        from unittest.mock import patch
        from animica_studio.services.rpc_client import RpcClient

        client = RpcClient("http://localhost:9999")
        # Guard must raise BEFORE any network call; verify call() is never reached.
        with patch.object(client, "call") as mock_call:
            with pytest.raises(TypeError, match="raw_tx_hex must be a hex str"):
                client.send_raw_tx({"rawTx": "0x1234"})  # type: ignore[arg-type]
        mock_call.assert_not_called()

    def test_none_param_raises_type_error(self):
        from unittest.mock import patch
        from animica_studio.services.rpc_client import RpcClient

        client = RpcClient("http://localhost:9999")
        with patch.object(client, "call") as mock_call:
            with pytest.raises(TypeError, match="raw_tx_hex must be a hex str"):
                client.send_raw_tx(None)  # type: ignore[arg-type]
        mock_call.assert_not_called()

    def test_valid_string_does_not_raise_locally(self):
        """A valid hex string should not raise before the network call."""
        from unittest.mock import patch
        from animica_studio.services.rpc_client import RpcClient

        client = RpcClient("http://localhost:9999")
        # Patch `call` so no actual network is needed
        with patch.object(client, "call", return_value="0x" + "ab" * 32):
            with patch.object(client, "_pick_method", return_value="tx_sendRawTransaction"):
                result = client.send_raw_tx("0x" + "ab" * 32)
        assert result == "0x" + "ab" * 32


class TestRpcMethodSelection:
    """Method selection: rpc.discover chooses tx_sendRawTransaction and sends params as ['0x...']."""

    def test_pick_method_uses_discover(self):
        """_pick_method returns tx_sendRawTransaction when discover lists it."""
        from unittest.mock import patch
        from animica_studio.services.rpc_client import RpcClient

        client = RpcClient("http://localhost:9999")
        discover_result = {"methods": [{"name": "tx_sendRawTransaction"}, {"name": "chain_getHead"}]}
        with patch.object(client, "discover", return_value=discover_result):
            chosen = client._pick_method("tx_sendRawTransaction", "tx.sendRawTransaction")
        assert chosen == "tx_sendRawTransaction"

    def test_send_raw_tx_passes_list_param(self):
        """send_raw_tx always sends params as [raw_tx_hex] (array, not dict)."""
        from unittest.mock import patch, call as mcall
        from animica_studio.services.rpc_client import RpcClient

        client = RpcClient("http://localhost:9999")
        tx_hex = "0x" + "ab" * 32
        discover_result = {"methods": [{"name": "tx_sendRawTransaction"}]}
        with patch.object(client, "discover", return_value=discover_result):
            with patch.object(client, "call", return_value=tx_hex) as mock_call:
                client.send_raw_tx(tx_hex)

        # The call must use the canonical method name and params as a list
        mock_call.assert_called_once_with("tx_sendRawTransaction", [tx_hex])

    def test_fallback_to_first_candidate_when_discover_empty(self):
        """When discover returns no methods, fall back to the first candidate."""
        from unittest.mock import patch
        from animica_studio.services.rpc_client import RpcClient

        client = RpcClient("http://localhost:9999")
        with patch.object(client, "discover", return_value={"methods": []}):
            chosen = client._pick_method("tx_sendRawTransaction", "tx.sendRawTransaction")
        assert chosen == "tx_sendRawTransaction"


class TestWalletCreateIncludesLabel:
    """Wallet create command includes --label flag (problem statement requirement)."""

    def test_build_create_wallet_args_includes_label_flag(self):
        """When CliOps is available, build_create_wallet_args always emits --label."""
        from animica_studio.storage.config import Config
        from animica_studio.services.wallet_service import WalletService

        mock_ops = MagicMock()
        mock_ops.build.return_value = [
            "wallet", "create", "--label", "My Wallet", "--alg", "dilithium3"
        ]

        ws = WalletService(Config())
        args, clean_label, scheme = ws.build_create_wallet_args("My Wallet", "dilithium3")

        assert "--label" in args
        assert "My Wallet" in args
        assert clean_label == "My Wallet"
        assert scheme == "dilithium3"

