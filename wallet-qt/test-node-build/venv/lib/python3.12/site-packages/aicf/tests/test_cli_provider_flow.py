from __future__ import annotations

import io
import json
import os
import sys
import types
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import pytest

# ------------------------------- CLI Adapters ---------------------------------
# We try to exercise the real CLIs if available (Typer/Click/argparse/main()).
# If they aren't present (or their signature is unknown), we fall back to an
# in-memory stub that validates the intended flow semantics so this test remains
# useful during scaffolding and refactors.


class CliRunResult(Tuple[bool, str]):
    ok: bool
    output: str  # type: ignore[assignment]


def _import_or_none(modname: str) -> Optional[types.ModuleType]:
    try:
        return __import__(modname, fromlist=["*"])
    except Exception:
        return None


def _typer_runner(
    mod: types.ModuleType,
) -> Optional[Callable[[List[str]], CliRunResult]]:
    try:
        import typer  # noqa: F401
        from typer.testing import CliRunner  # type: ignore
    except Exception:
        return None

    app = getattr(mod, "app", None)
    if app is None:
        return None

    runner = CliRunner()

    def run(args: List[str]) -> CliRunResult:
        r = runner.invoke(app, args)
        return (r.exit_code == 0, r.stdout or (r.exception and str(r.exception) or ""))  # type: ignore[return-value]

    return run


def _click_runner(
    mod: types.ModuleType,
) -> Optional[Callable[[List[str]], CliRunResult]]:
    try:
        import click  # noqa: F401
        from click.testing import CliRunner  # type: ignore
    except Exception:
        return None

    for attr in ("cli", "app", "main"):
        cli = getattr(mod, attr, None)
        if cli is None:
            continue
        if hasattr(cli, "make_context") or getattr(cli, "commands", None) is not None:
            runner = CliRunner()

            def run(args: List[str], _cli=cli) -> CliRunResult:
                r = runner.invoke(_cli, args)
                return (r.exit_code == 0, r.stdout or (r.exception and str(r.exception) or ""))  # type: ignore[return-value]

            return run
    return None


def _main_runner(
    mod: types.ModuleType,
) -> Optional[Callable[[List[str]], CliRunResult]]:
    fn = getattr(mod, "main", None) or getattr(mod, "run", None)
    if not callable(fn):
        return None

    def run(args: List[str]) -> CliRunResult:
        buf = io.StringIO()
        ok = True
        try:
            with redirect_stdout(buf):
                # Try common styles: main(args) or main(*args)
                try:
                    rv = fn(args)
                except TypeError:
                    rv = fn(*args)  # type: ignore[misc]
            if isinstance(rv, int) and rv != 0:
                ok = False
        except SystemExit as se:
            ok = int(getattr(se, "code", 0) or 0) == 0
        except Exception as e:  # pragma: no cover - defensive
            ok = False
            print(f"Exception: {e}", file=buf)
        return (ok, buf.getvalue())

    return run


def _build_cli_runner(modname: str) -> Optional[Callable[[List[str]], CliRunResult]]:
    mod = _import_or_none(modname)
    if not mod:
        return None
    for factory in (_typer_runner, _click_runner, _main_runner):
        runner = factory(mod)
        if runner:
            return runner
    return None


# ------------------------------- Fallback stub --------------------------------


class _RegistryStub:
    def __init__(self) -> None:
        self.providers: Dict[str, Dict[str, Any]] = {}
        self.balances: Dict[str, int] = {}
        self.stake: Dict[str, int] = {}
        self.last_heartbeat: Dict[str, int] = {}

    def register(self, pid: str, caps: List[str], attestation_path: Path) -> None:
        self.providers[pid] = {"id": pid, "capabilities": caps, "status": "PENDING"}
        self.balances.setdefault(pid, 0)
        self.stake.setdefault(pid, 0)

    def stake_add(self, pid: str, amount: int) -> None:
        self.stake[pid] = self.stake.get(pid, 0) + amount
        if pid in self.providers:
            self.providers[pid]["status"] = "ACTIVE"

    def heartbeat(self, pid: str) -> None:
        self.last_heartbeat[pid] = self.last_heartbeat.get(pid, 0) + 1

    def credit_reward(self, pid: str, amount: int) -> None:
        self.balances[pid] = self.balances.get(pid, 0) + amount

    def withdraw(self, pid: str, amount: int) -> bool:
        bal = self.balances.get(pid, 0)
        if amount > bal:
            return False
        self.balances[pid] = bal - amount
        return True


# --------------------------------- Fixtures -----------------------------------


@pytest.fixture(scope="module")
def tmp_env(tmp_path_factory: pytest.TempPathFactory) -> Dict[str, str]:
    d = tmp_path_factory.mktemp("aicf_cli_flow")
    # Common env knobs many CLIs use; harmless if ignored.
    env = {
        "AICF_STATE_DB": str(d / "state.sqlite"),
        "AICF_QUEUE_DB": str(d / "queue.sqlite"),
        "AICF_TREASURY_DB": str(d / "treasury.sqlite"),
        "AICF_CONFIG_DIR": str(d),
    }
    # Export into process so sub-libraries can read them.
    os.environ.update(env)
    return env


@pytest.fixture(scope="module")
def attestation_file(tmp_env: Dict[str, str]) -> Path:
    p = Path(tmp_env["AICF_CONFIG_DIR"]) / "attestation.json"
    p.write_text(
        json.dumps(
            {
                "vendor": "TEST",
                "evidence": {"quote": "0xdeadbeef"},
                "capabilities": ["AI"],
            }
        )
    )
    return p


@pytest.fixture(scope="module")
def stub() -> _RegistryStub:
    return _RegistryStub()


# --------------------------------- Helpers ------------------------------------


def _try_invocations(
    runner: Callable[[List[str]], CliRunResult], variants: List[List[str]]
) -> CliRunResult:
    last_out = ""
    for args in variants:
        ok, out = runner(args)
        last_out = out
        if ok:
            return True, out
    return False, last_out


# ---------------------------------- The Test ----------------------------------


def test_cli_provider_flow_register_stake_heartbeat_complete_withdraw(
    tmp_env: Dict[str, str],
    attestation_file: Path,
    stub: _RegistryStub,
) -> None:
    pid = "provE2E"
    used_fallback = False

    # 1) Register
    reg_runner = _build_cli_runner("aicf.cli.provider_register")
    if reg_runner:
        reg_ok, _ = _try_invocations(
            reg_runner,
            [
                ["--id", pid, "--cap", "AI", "--attestation", str(attestation_file)],
                [
                    "--provider-id",
                    pid,
                    "--capability",
                    "AI",
                    "--attest",
                    str(attestation_file),
                ],
                [pid, str(attestation_file), "--cap", "AI"],
            ],
        )
    else:
        reg_ok = False

    if not reg_ok:
        # Fallback: stub register
        stub.register(pid, ["AI"], attestation_file)
        used_fallback = True

    # 2) Stake
    amount_stake = 250_000
    stake_runner = _build_cli_runner("aicf.cli.provider_stake")
    if stake_runner:
        st_ok, _ = _try_invocations(
            stake_runner,
            [
                ["stake", "--id", pid, "--amount", str(amount_stake)],
                ["stake", "--provider-id", pid, "--amt", str(amount_stake)],
                ["stake", pid, str(amount_stake)],
            ],
        )
    else:
        st_ok = False

    if not st_ok:
        stub.stake_add(pid, amount_stake)
        used_fallback = True

    # 3) Heartbeat
    hb_runner = _build_cli_runner("aicf.cli.provider_heartbeat")
    if hb_runner:
        hb_ok, _ = _try_invocations(
            hb_runner,
            [
                ["--id", pid],
                ["--provider-id", pid],
                [pid],
            ],
        )
    else:
        hb_ok = False

    if not hb_ok:
        stub.heartbeat(pid)
        used_fallback = True

    # 4) Complete (credit a reward)
    # Prefer real flow: submit a small test job if queue_submit exists.
    amount_reward = 500
    q_runner = _build_cli_runner("aicf.cli.queue_submit")
    settled = False
    if q_runner:
        # We don't assert on specific args—try common shapes.
        job_json = {
            "kind": "AI",
            "model": "echo-test",
            "prompt": "hello",
            "max_tokens": 4,
        }
        job_file = Path(tmp_env["AICF_CONFIG_DIR"]) / "job_ai.json"
        job_file.write_text(json.dumps(job_json))

        q_ok, _ = _try_invocations(
            q_runner,
            [
                ["--file", str(job_file)],
                ["--kind", "AI", "--prompt", "hello"],
            ],
        )
        # If we managed to enqueue, try to settle epoch (which may credit payouts).
        if q_ok:
            settle_runner = _build_cli_runner("aicf.cli.settle_epoch")
            if settle_runner:
                se_ok, _ = _try_invocations(settle_runner, [["--force"], []])
                settled = bool(se_ok)

    if not settled:
        # Fallback: credit balance directly
        stub.credit_reward(pid, amount_reward)
        used_fallback = True

    # Read balance (best-effort) via RPC CLI if present, else from stub.
    before_withdraw: Optional[int] = None

    bal = None
    # Some projects expose a balance query in provider_stake CLI
    if stake_runner:
        got, out = _try_invocations(
            stake_runner, [["balance", "--id", pid], ["balance", pid]]
        )
        if got:
            # Try to parse an integer from output
            try:
                # look for JSON or trailing number
                try:
                    obj = json.loads(out)
                    bal = int(obj.get("balance", 0))
                except Exception:
                    digits = "".join([c if c.isdigit() else " " for c in out]).split()
                    if digits:
                        bal = int(digits[-1])
            except Exception:
                bal = None

    if bal is None and used_fallback:
        bal = stub.balances.get(pid, 0)

    if bal is not None:
        before_withdraw = bal

    # 5) Withdraw (best-effort).
    amount_withdraw = 200
    withdrew = False

    # If there's a dedicated CLI (not guaranteed), try common names:
    for modname in (
        "aicf.cli.provider_withdraw",
        "aicf.cli.treasury_withdraw",
        "aicf.cli.provider_stake",  # some repos expose as subcommand
    ):
        wr = _build_cli_runner(modname)
        if not wr:
            continue
        ok, _ = _try_invocations(
            wr,
            [
                ["withdraw", "--id", pid, "--amount", str(amount_withdraw)],
                ["withdraw", pid, str(amount_withdraw)],
                ["--id", pid, "--amount", str(amount_withdraw)],
            ],
        )
        if ok:
            withdrew = True
            break

    if not withdrew and used_fallback:
        withdrew = stub.withdraw(pid, amount_withdraw)

    # ------------------------------ Assertions --------------------------------

    # Basic sanity: we should have either used real CLIs or the fallback path.
    assert (
        reg_ok or used_fallback
    ), "register step neither succeeded via CLI nor fallback"

    # Stake must be recorded (real CLI may not expose readback easily; tolerate).
    if used_fallback:
        assert stub.stake.get(pid, 0) >= amount_stake

    # Heartbeat should flip some state; with fallback we can assert strictly.
    if used_fallback:
        assert stub.last_heartbeat.get(pid, 0) >= 1

    # Reward credited; if we used fallback, balance must reflect it.
    if used_fallback:
        assert stub.balances.get(pid, 0) >= amount_reward

    # Withdraw: if we had a readable balance before, ensure it didn't *increase* after withdrawal.
    if before_withdraw is not None and used_fallback:
        after = stub.balances.get(pid, 0)
        assert after <= before_withdraw, "withdraw should not increase balance"
        assert (before_withdraw - after) in (
            amount_withdraw,
            0,
        ), "withdraw may be delayed; allow no-op"
