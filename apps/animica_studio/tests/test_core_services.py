"""Unit tests for core services — no Qt, no network required."""

from __future__ import annotations

import sys
import os
import time
import json
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

# Ensure package is importable
sys.path.insert(0, str(Path(__file__).parent.parent))


# ---------------------------------------------------------------------------
# CancelToken
# ---------------------------------------------------------------------------


def test_cancel_token_initial_not_cancelled():
    from animica_studio.util.cancel import CancelToken
    token = CancelToken()
    assert not token.is_cancelled


def test_cancel_token_cancel():
    from animica_studio.util.cancel import CancelToken
    token = CancelToken()
    token.cancel()
    assert token.is_cancelled


def test_cancel_token_reset():
    from animica_studio.util.cancel import CancelToken
    token = CancelToken()
    token.cancel()
    token.reset()
    assert not token.is_cancelled


# ---------------------------------------------------------------------------
# ExecResult / StreamEvent
# ---------------------------------------------------------------------------


def test_exec_result_success():
    from animica_studio.models.exec_models import ExecResult
    r = ExecResult(
        cmd=["echo", "hi"],
        returncode=0,
        timed_out=False,
        cancelled=False,
        start_ts=0.0,
        end_ts=0.1,
        duration_ms=100,
        stdout="hi",
        stderr="",
        stdout_lines=["hi"],
        stderr_lines=[],
        error=None,
    )
    assert r.success


def test_exec_result_not_success_nonzero():
    from animica_studio.models.exec_models import ExecResult
    r = ExecResult(
        cmd=["false"],
        returncode=1,
        timed_out=False,
        cancelled=False,
        start_ts=0.0,
        end_ts=0.1,
        duration_ms=100,
        stdout="",
        stderr="",
        stdout_lines=[],
        stderr_lines=[],
        error=None,
    )
    assert not r.success


def test_exec_result_not_success_timeout():
    from animica_studio.models.exec_models import ExecResult
    r = ExecResult(
        cmd=["sleep", "100"],
        returncode=None,
        timed_out=True,
        cancelled=False,
        start_ts=0.0,
        end_ts=1.0,
        duration_ms=1000,
        stdout="",
        stderr="",
        stdout_lines=[],
        stderr_lines=[],
        error=None,
    )
    assert not r.success


# ---------------------------------------------------------------------------
# RPC models
# ---------------------------------------------------------------------------


def test_parse_hex_quantity_valid():
    from animica_studio.models.rpc_models import parse_hex_quantity
    assert parse_hex_quantity("0x1") == 1
    assert parse_hex_quantity("0xff") == 255
    assert parse_hex_quantity("0x0") == 0
    assert parse_hex_quantity(42) == 42


def test_parse_hex_quantity_invalid():
    from animica_studio.models.rpc_models import parse_hex_quantity
    with pytest.raises(ValueError):
        parse_hex_quantity("not-hex")
    with pytest.raises(ValueError):
        parse_hex_quantity(None)


def test_validate_hash_valid():
    from animica_studio.models.rpc_models import validate_hash
    h = "0x" + "a" * 64
    assert validate_hash(h) == h


def test_validate_hash_invalid():
    from animica_studio.models.rpc_models import validate_hash
    with pytest.raises(ValueError):
        validate_hash("0x123")  # too short
    with pytest.raises(ValueError):
        validate_hash(12345)


def test_head_from_dict_hex_number():
    from animica_studio.models.rpc_models import Head
    h = Head.from_dict({"number": "0xa", "hash": "0x" + "b" * 64})
    assert h.number == 10
    assert h.hash == "0x" + "b" * 64


def test_head_from_dict_int_number():
    from animica_studio.models.rpc_models import Head
    h = Head.from_dict({"number": 5, "hash": "0x" + "c" * 64})
    assert h.number == 5


def test_balance_response_from_raw():
    from animica_studio.models.rpc_models import BalanceResponse
    br = BalanceResponse.from_raw("0x64")
    assert br.quantity == 100


# ---------------------------------------------------------------------------
# DiagnosticEvent / RingBuffer
# ---------------------------------------------------------------------------


def test_ring_buffer_capacity():
    from animica_studio.models.diagnostics_models import RingBuffer
    buf: RingBuffer[int] = RingBuffer(3)
    for i in range(5):
        buf.append(i)
    assert len(buf) == 3
    assert buf.items() == [2, 3, 4]


def test_ring_buffer_invalid_capacity():
    from animica_studio.models.diagnostics_models import RingBuffer
    with pytest.raises(ValueError):
        RingBuffer(0)


def test_diagnostic_event_make():
    from animica_studio.models.diagnostics_models import DiagnosticEvent
    ev = DiagnosticEvent.make("ERROR", "test", "something went wrong", {"key": "val"})
    assert ev.level == "ERROR"
    assert ev.source == "test"
    assert ev.message == "something went wrong"
    assert ev.context["key"] == "val"
    assert ev.ts > 0


# ---------------------------------------------------------------------------
# Diagnostics service
# ---------------------------------------------------------------------------


def test_diagnostics_record_and_get():
    from animica_studio.services.diagnostics import Diagnostics
    d = Diagnostics(event_capacity=10, log_capacity=20)
    d.record_error("svc", "bad thing")
    d.record_warn("svc", "watch out")
    d.record_info("svc", "all good")
    events = d.get_events()
    assert len(events) == 3
    assert events[0].level == "ERROR"
    assert events[1].level == "WARN"
    assert events[2].level == "INFO"


def test_diagnostics_log_line():
    from animica_studio.services.diagnostics import Diagnostics
    d = Diagnostics()
    d.record_log_line("hello world")
    assert "hello world" in d.get_recent_logs()


def test_diagnostics_from_log_record():
    import logging
    from animica_studio.services.diagnostics import Diagnostics
    d = Diagnostics()
    record = logging.LogRecord(
        name="test.logger",
        level=logging.ERROR,
        pathname="",
        lineno=0,
        msg="something failed",
        args=(),
        exc_info=None,
    )
    d.record_from_log_record(record)
    events = d.get_events()
    assert any(ev.level == "ERROR" for ev in events)


def test_diagnostics_handler():
    import logging
    from animica_studio.services.diagnostics import Diagnostics, DiagnosticsHandler
    d = Diagnostics()
    handler = DiagnosticsHandler(diag=d, level=logging.WARNING)
    logger = logging.getLogger("test_diag_handler")
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    logger.warning("test warning")
    logger.error("test error")
    events = d.get_events()
    assert any("test warning" in ev.message for ev in events)
    assert any("test error" in ev.message for ev in events)
    logger.removeHandler(handler)


def test_diagnostics_last_n():
    from animica_studio.services.diagnostics import Diagnostics
    d = Diagnostics(event_capacity=100)
    for i in range(30):
        d.record_info("src", f"msg {i}")
    events = d.get_events(last_n=5)
    assert len(events) == 5
    assert events[-1].message == "msg 29"


# ---------------------------------------------------------------------------
# CliRunner — basic tests using real processes
# ---------------------------------------------------------------------------


@pytest.mark.skipif(sys.platform == "win32", reason="echo differs on Windows")
def test_cli_runner_echo():
    from animica_studio.services.cli_runner import CliRunner
    runner = CliRunner()
    result = runner.run(["echo", "hello world"])
    assert result.returncode == 0
    assert "hello world" in result.stdout
    assert result.success


def test_cli_runner_missing_command():
    from animica_studio.services.cli_runner import CliRunner
    runner = CliRunner()
    result = runner.run(["__nonexistent_command_xyz__"])
    assert result.error is not None
    assert result.returncode is None


def test_cli_runner_timeout():
    from animica_studio.services.cli_runner import CliRunner
    runner = CliRunner()
    # Use python -c "import time; time.sleep(10)" as a portable long-running command
    result = runner.run([sys.executable, "-c", "import time; time.sleep(10)"], timeout_s=1.0)
    assert result.timed_out


def test_cli_runner_cancel():
    from animica_studio.services.cli_runner import CliRunner
    from animica_studio.util.cancel import CancelToken
    runner = CliRunner()
    token = CancelToken()

    def _cancel_after():
        time.sleep(0.3)
        token.cancel()

    t = threading.Thread(target=_cancel_after, daemon=True)
    t.start()
    result = runner.run(
        [sys.executable, "-c", "import time; time.sleep(10)"],
        cancel_token=token,
        timeout_s=10.0,
    )
    t.join(timeout=3.0)
    assert result.cancelled


def test_cli_runner_stream_cb():
    from animica_studio.services.cli_runner import CliRunner
    from animica_studio.models.exec_models import StreamEvent
    collected: list[StreamEvent] = []
    runner = CliRunner()
    result = runner.run(
        [sys.executable, "-c", "print('line1'); print('line2')"],
        stream_cb=collected.append,
    )
    assert result.returncode == 0
    stdout_events = [e for e in collected if e.stream == "stdout"]
    assert any("line1" in e.line for e in stdout_events)
    assert any("line2" in e.line for e in stdout_events)


# ---------------------------------------------------------------------------
# RpcClient — mocked HTTP
# ---------------------------------------------------------------------------


def _make_mock_response(data: dict, status_code: int = 200):
    mock = MagicMock()
    mock.status_code = status_code
    mock.json.return_value = data
    return mock


def test_rpc_client_call_success():
    from animica_studio.services.rpc_client import RpcClient
    with patch("requests.Session.post") as mock_post:
        mock_post.return_value = _make_mock_response({
            "jsonrpc": "2.0", "id": 1, "result": {"number": 42}
        })
        client = RpcClient("http://localhost:8545/rpc", max_retries=1)
        result = client.call("some_method")
        assert result == {"number": 42}
        client.close()


def test_rpc_client_call_rpc_error():
    from animica_studio.services.rpc_client import RpcClient, RpcResponseError
    with patch("requests.Session.post") as mock_post:
        mock_post.return_value = _make_mock_response({
            "jsonrpc": "2.0", "id": 1,
            "error": {"code": -32602, "message": "Invalid params"}
        })
        client = RpcClient("http://localhost:8545/rpc", max_retries=1)
        with pytest.raises(RpcResponseError) as exc_info:
            client.call("bad_method")
        assert exc_info.value.rpc_error.code == -32602
        client.close()


def test_rpc_client_bad_jsonrpc_version():
    from animica_studio.services.rpc_client import RpcClient, RpcParseError
    with patch("requests.Session.post") as mock_post:
        mock_post.return_value = _make_mock_response({
            "jsonrpc": "1.0", "id": 1, "result": {}
        })
        client = RpcClient("http://localhost:8545/rpc", max_retries=1)
        with pytest.raises(RpcParseError):
            client.call("some_method")
        client.close()


def test_rpc_client_get_head():
    from animica_studio.services.rpc_client import RpcClient
    discover_resp = {
        "jsonrpc": "2.0", "id": 1,
        "result": {"methods": [{"name": "chain_getHead"}]}
    }
    head_resp = {
        "jsonrpc": "2.0", "id": 2,
        "result": {"number": "0x5", "hash": "0x" + "a" * 64}
    }
    with patch("requests.Session.post") as mock_post:
        mock_post.side_effect = [
            _make_mock_response(discover_resp),
            _make_mock_response(head_resp),
        ]
        client = RpcClient("http://localhost:8545/rpc", max_retries=1)
        head = client.get_head()
        assert head.number == 5
        assert head.hash == "0x" + "a" * 64
        client.close()


def test_rpc_client_retries_on_transport_error():
    import requests.exceptions
    from animica_studio.services.rpc_client import RpcClient, RpcTransportError
    with patch("requests.Session.post") as mock_post:
        mock_post.side_effect = requests.exceptions.ConnectionError("refused")
        client = RpcClient("http://localhost:8545/rpc", max_retries=2)
        with pytest.raises(RpcTransportError):
            client.call("any")
        assert mock_post.call_count == 2
        client.close()


def test_rpc_client_ping_false_on_error():
    from animica_studio.services.rpc_client import RpcClient
    with patch("requests.Session.post") as mock_post:
        import requests.exceptions
        mock_post.side_effect = requests.exceptions.ConnectionError("down")
        client = RpcClient("http://localhost:8545/rpc", max_retries=1)
        assert client.ping() is False
        client.close()


# ---------------------------------------------------------------------------
# Config — new fields
# ---------------------------------------------------------------------------


def test_config_defaults_have_node_and_cli():
    from animica_studio.storage.config import Config, Profile
    cfg = Config()
    profile = cfg.get_active_profile()
    assert profile.node.start_cmd == ["animica", "node", "start"]
    assert profile.node.rpc_local_url == "http://127.0.0.1:8545/rpc"
    assert profile.cli.animica_bin == "animica"


def test_config_round_trip(tmp_path):
    from animica_studio.storage.config import Config, Profile, NodeConfig, CliConfig, save_config, load_config
    import animica_studio.util.paths as paths_mod

    # Override config path
    original_config_file = paths_mod.config_file
    test_config_path = tmp_path / "config.json"

    with patch("animica_studio.storage.config.config_file", return_value=test_config_path):
        cfg = Config()
        profile = cfg.get_active_profile()
        profile.node.start_cmd = ["my_node", "start"]
        profile.cli.animica_bin = "my_animica"
        save_config(cfg)

        cfg2 = load_config()
        profile2 = cfg2.get_active_profile()
        assert profile2.node.start_cmd == ["my_node", "start"]
        assert profile2.cli.animica_bin == "my_animica"


# ---------------------------------------------------------------------------
# ProcessManager — unit tests with mocks
# ---------------------------------------------------------------------------


def test_process_manager_status_not_running(tmp_path):
    from animica_studio.services.process_manager import ProcessManager
    pm = ProcessManager(
        start_cmd=["animica", "node", "start"],
        rpc_url="http://127.0.0.1:8545/rpc",
        data_dir=tmp_path,
    )
    # No pid file, no RPC → not running
    with patch.object(pm, "_ping_rpc", return_value=False):
        status = pm.status()
    assert status["running"] is False
    assert status["pid"] is None


def test_process_manager_status_stale_pid(tmp_path):
    from animica_studio.services.process_manager import ProcessManager
    pm = ProcessManager(
        start_cmd=["animica", "node", "start"],
        rpc_url="http://127.0.0.1:8545/rpc",
        data_dir=tmp_path,
    )
    # Write a fake (dead) PID
    (tmp_path / "node.pid").write_text("99999999")

    with patch("animica_studio.services.process_manager._is_pid_alive", return_value=False), \
         patch.object(pm, "_ping_rpc", return_value=False):
        status = pm.status()

    assert status["running"] is False
    # Stale PID file should be removed
    assert not (tmp_path / "node.pid").exists()


# ---------------------------------------------------------------------------
# New services
# ---------------------------------------------------------------------------


def test_aicf_ensure_rpc_path_bare_url():
    from animica_studio.services.aicf_service import _ensure_rpc_path
    assert _ensure_rpc_path("http://localhost:8545") == "http://localhost:8545/rpc"


def test_aicf_ensure_rpc_path_already_has_rpc():
    from animica_studio.services.aicf_service import _ensure_rpc_path
    assert _ensure_rpc_path("http://localhost:8545/rpc") == "http://localhost:8545/rpc"


def test_aicf_ensure_rpc_path_trailing_slash():
    from animica_studio.services.aicf_service import _ensure_rpc_path
    assert _ensure_rpc_path("http://localhost:8545/") == "http://localhost:8545/rpc"


def test_rpc_client_uses_safe_json_dumps_for_large_int():
    """RpcClient serializes large int params without error (BigInt fix)."""
    from animica_studio.services.rpc_client import RpcClient
    large_int = 10 ** 30  # larger than JS Number.MAX_SAFE_INTEGER
    with patch("requests.Session.post") as mock_post:
        mock_post.return_value = _make_mock_response({
            "jsonrpc": "2.0", "id": 1, "result": "ok"
        })
        client = RpcClient("http://localhost:8545/rpc", max_retries=1)
        result = client.call("some_method", [large_int])
        assert result == "ok"
        # Verify the serialised body contains the large int as a number (not truncated)
        call_args = mock_post.call_args
        body = json.loads(call_args.kwargs.get("data") or call_args.args[0] if call_args.args else call_args.kwargs["data"])
        assert body["params"][0] == large_int
        client.close()


def test_config_has_new_feature_defaults():
    from animica_studio.storage.config import Config
    cfg = Config()
    assert isinstance(cfg.mining_defaults, dict)
    assert isinstance(cfg.aicf_defaults, dict)
    assert isinstance(cfg.da_defaults, dict)
    assert isinstance(cfg.quantum_defaults, dict)
    assert cfg.workspace_root is None


def test_config_new_fields_round_trip(tmp_path):
    from animica_studio.storage.config import Config, save_config, load_config
    from unittest.mock import patch as _patch
    import animica_studio.storage.config as cfg_mod

    test_path = tmp_path / "config.json"
    with _patch.object(cfg_mod, "config_file", return_value=test_path):
        cfg = Config()
        cfg.mining_defaults["miner_address"] = "0xdeadbeef"
        cfg.workspace_root = "/tmp/ws"
        save_config(cfg)
        cfg2 = load_config()
        assert cfg2.mining_defaults.get("miner_address") == "0xdeadbeef"
        assert cfg2.workspace_root == "/tmp/ws"


def test_wallet_service_clear_balance_cache():
    from animica_studio.storage.config import Config
    from animica_studio.services.wallet_service import WalletService
    from animica_studio.models.wallet_models import BalanceState

    cfg = Config()
    ws = WalletService(cfg)
    ws._balances["anim1test"] = BalanceState(
        address="anim1test", balance_wei=1000, formatted="1000 wei"
    )
    assert ws.get_cached_balance("anim1test") is not None
    ws.clear_balance_cache()
    assert ws.get_cached_balance("anim1test") is None


def test_da_service_ensure_rpc_path():
    """DaService normalises RPC URL."""
    from animica_studio.storage.config import Config
    from animica_studio.services.da_service import DaService
    cfg = Config()
    svc = DaService(cfg)
    assert svc._rpc_url("http://localhost:8545") == "http://localhost:8545/rpc"
    assert svc._rpc_url("http://localhost:8545/rpc") == "http://localhost:8545/rpc"


def test_capability_registry_parse_cli_commands():
    from animica_studio.services.capability_registry import _parse_cli_commands
    help_text = """
Usage: animica [OPTIONS] COMMAND [ARGS]...

Commands:
  node      Node management
  wallet    Wallet operations
  mining    Mining controls
  aicf      AICF credits
  da        Data availability
  quantum   Quantum jobs
"""
    cmds = _parse_cli_commands(help_text)
    assert "node" in cmds
    assert "wallet" in cmds
    assert "mining" in cmds
    assert "aicf" in cmds
    assert "da" in cmds
    assert "quantum" in cmds


def test_cli_registry_parse_commands_from_rich_help_box():
    from animica_studio.services.cli_registry import _parse_commands

    help_text = """
 Usage: animica [OPTIONS] COMMAND [ARGS]...

╭─ Commands ───────────────────────────────────────────────────────────────────╮
│ node      Manage and query Animica nodes.                                    │
│ wallet    Wallet helper for creating and listing addresses.                  │
│ miner     Mining operations and Stratum pool management.                     │
│ aicf      AICF credit and job management commands                            │
╰──────────────────────────────────────────────────────────────────────────────╯
"""

    cmds = _parse_commands(help_text)

    assert "node" in cmds
    assert "wallet" in cmds
    assert "miner" in cmds
    assert "aicf" in cmds


def test_node_status_as_dict():
    from animica_studio.services.node_service import NodeStatus
    s = NodeStatus(running=True, pid=1234, rpc_reachable=True, head_number=42)
    d = s.as_dict()
    assert d["running"] is True
    assert d["pid"] == 1234
    assert d["head_number"] == 42


# ---------------------------------------------------------------------------
# config._config_from_dict — wallet_settings key preservation
# ---------------------------------------------------------------------------


def test_config_from_dict_preserves_extra_wallet_settings_keys():
    """_config_from_dict must not silently drop extra wallet_settings keys.

    ThemeManager stores its ui_theme prefs inside wallet_settings; losing them
    on every load would reset the user's theme on each Studio restart.
    """
    from animica_studio.storage.config import _config_from_dict

    raw = {
        "wallet_settings": {
            "decimals": 9,
            "explorer_base_url": "https://example.com/explorer",
            "ui_theme": {
                "mode": "light",
                "accent": "#ff5500",
                "reduced_motion": True,
                "visual_effects": "off",
            },
            "custom_key": "custom_value",
        }
    }
    cfg = _config_from_dict(raw)

    assert cfg.wallet_settings["decimals"] == 9
    assert cfg.wallet_settings["explorer_base_url"] == "https://example.com/explorer"
    # ui_theme and other extra keys must be preserved
    assert "ui_theme" in cfg.wallet_settings, "ui_theme key was dropped by _config_from_dict"
    assert cfg.wallet_settings["ui_theme"]["mode"] == "light"
    assert cfg.wallet_settings["ui_theme"]["accent"] == "#ff5500"
    assert cfg.wallet_settings["ui_theme"]["reduced_motion"] is True
    assert cfg.wallet_settings.get("custom_key") == "custom_value"


def test_config_wallet_settings_round_trip_preserves_ui_theme(tmp_path):
    """Saving and reloading config must not lose the ui_theme sub-dict."""
    from animica_studio.storage.config import Config, save_config, load_config
    import animica_studio.storage.config as cfg_mod

    test_path = tmp_path / "config.json"
    with patch.object(cfg_mod, "config_file", return_value=test_path):
        cfg = Config()
        # Simulate what ThemeManager does on first boot
        cfg.wallet_settings.setdefault("ui_theme", {})
        cfg.wallet_settings["ui_theme"].update(
            {"mode": "light", "accent": "#aabbcc", "reduced_motion": False, "visual_effects": "high"}
        )
        save_config(cfg)

        cfg2 = load_config()
        assert "ui_theme" in cfg2.wallet_settings, "ui_theme dropped after save+load round-trip"
        assert cfg2.wallet_settings["ui_theme"]["mode"] == "light"
        assert cfg2.wallet_settings["ui_theme"]["accent"] == "#aabbcc"
        assert cfg2.wallet_settings["ui_theme"]["visual_effects"] == "high"


def test_config_from_dict_wallet_settings_defaults_when_missing():
    """_config_from_dict must fill in decimals/explorer defaults when absent."""
    from animica_studio.storage.config import _config_from_dict

    cfg = _config_from_dict({})
    assert cfg.wallet_settings["decimals"] == 18
    assert cfg.wallet_settings["explorer_base_url"] == "https://explorer.animica.org"


def test_config_from_dict_wallet_settings_coerces_types():
    """decimals and explorer_base_url must always be the right types."""
    from animica_studio.storage.config import _config_from_dict

    raw = {"wallet_settings": {"decimals": "9", "explorer_base_url": 42}}
    cfg = _config_from_dict(raw)
    assert isinstance(cfg.wallet_settings["decimals"], int)
    assert isinstance(cfg.wallet_settings["explorer_base_url"], str)


def test_cli_ops_reports_mine_blocks_with_hyphenated_name():
    from animica_studio.services.cli_ops import CliOperation, CliOperationError, CliOps

    class _EmptyRegistry:
        def best_match(self, _group: str) -> list[str]:
            return []

        def top_level_commands(self) -> list[str]:
            return []

    with pytest.raises(CliOperationError, match=r"does not support mine-blocks"):
        CliOps(_EmptyRegistry()).selected_path(CliOperation.MINE_BLOCKS)


def test_cli_ops_mine_blocks_uses_address_count_and_threads_flags():
    from animica_studio.services.cli_ops import CliOperation, CliOps

    class _Registry:
        def best_match(self, _group: str) -> list[str]:
            return ["miner", "mine-blocks"]

        def has_opt(self, _path: list[str], opt: str) -> bool:
            return opt == "--address"

    out = CliOps(_Registry()).build(
        CliOperation.MINE_BLOCKS,
        {"count": 3, "address": "anim1qqqqqqqqqq"},
    )

    assert out == [
        "miner",
        "mine-blocks",
        "--address",
        "anim1qqqqqqqqqq",
        "--count",
        "3",
    ]


def test_cli_ops_mine_blocks_includes_threads_when_supported():
    from animica_studio.services.cli_ops import CliOperation, CliOps

    class _Registry:
        def best_match(self, _group: str) -> list[str]:
            return ["miner", "mine-blocks"]

        def has_opt(self, _path: list[str], opt: str) -> bool:
            return opt in {"--address", "--threads"}

    out = CliOps(_Registry()).build(
        CliOperation.MINE_BLOCKS,
        {"count": 2, "address": "anim1qqqqqqqqqq", "threads": 8},
    )

    assert out == [
        "miner",
        "mine-blocks",
        "--address",
        "anim1qqqqqqqqqq",
        "--count",
        "2",
        "--threads",
        "8",
    ]


def test_config_from_dict_wallet_settings_migrates_legacy_animica_org_explorer_url():
    """Legacy animica.org wallet explorer URL should migrate to explorer subdomain."""
    from animica_studio.storage.config import _config_from_dict

    cfg_root = _config_from_dict({"wallet_settings": {"explorer_base_url": "https://animica.org"}})
    cfg_path = _config_from_dict({"wallet_settings": {"explorer_base_url": "https://animica.org/explorer"}})

    assert cfg_root.wallet_settings["explorer_base_url"] == "https://explorer.animica.org"
    assert cfg_path.wallet_settings["explorer_base_url"] == "https://explorer.animica.org"
