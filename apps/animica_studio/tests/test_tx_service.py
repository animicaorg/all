from __future__ import annotations

from subprocess import CompletedProcess

from animica_studio.services.tx_service import TxService
from animica_studio.storage.config import load_config


def test_validate_to_address_supports_anim_and_hex() -> None:
    assert TxService.validate_to_address("anim1qqqqqqqqqqqqqqqq")
    assert TxService.validate_to_address("0x" + "a" * 40)
    assert not TxService.validate_to_address("")
    assert not TxService.validate_to_address("0x123")


def test_parse_amount_positive_and_reject_zero() -> None:
    assert TxService.parse_amount("1") == 10**18
    assert TxService.parse_amount("0.5") == 5 * 10**17

    try:
        TxService.parse_amount("0")
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "greater than zero" in str(exc)


def test_send_via_cli_parses_tx_hash(monkeypatch) -> None:
    cfg = load_config()
    svc = TxService(cfg)

    monkeypatch.setattr(
        "animica_studio.services.tx_service.resolve_animica_cli_program_and_env",
        lambda _cfg: ("animica", [], {}),
    )

    def _fake_run(*args, **kwargs):
        return CompletedProcess(args=args[0], returncode=0, stdout="submitted 0x" + "b" * 64, stderr="")

    monkeypatch.setattr("animica_studio.services.tx_service.subprocess.run", _fake_run)

    result = svc.send_via_cli(
        from_addr="anim1qqqqqqqqqqqqqqqq",
        to_addr="anim1qqqqqqqqqqqqqqqq",
        amount_wei=10**18,
        rpc_url="http://127.0.0.1:8545",
        chain_id=1,
    )
    assert result.ok
    assert result.tx_hash == "0x" + "b" * 64
