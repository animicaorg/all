"""
animica quantum quw — Quantum Useful Work operator commands.

Run an attested-QRNG useful-work node: detect hardware, self-test the lane,
health-check raw entropy, and run the contribution worker that earns rewards by
feeding hardware-attested quantum entropy into the chain randomness beacon.
"""

from __future__ import annotations

import json
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

console = Console()

quw_app = typer.Typer(
    name="quw",
    help="Quantum Useful Work: contribute hardware-attested QRNG entropy and earn rewards",
    no_args_is_help=True,
)


@quw_app.command("detect")
def detect(json_output: bool = typer.Option(False, "--json")):
    """Detect available entropy sources (IDQ Quantis / hwrng / software fallback)."""
    from randomness.qrng import providers
    sources = [s.info().as_dict() for s in providers.detect_sources()]
    if json_output:
        console.print_json(json.dumps(sources)); return
    t = Table(title="Quantum entropy sources (best first)")
    for col in ("name", "vendor", "model", "hardware", "quantum", "attested", "device"):
        t.add_column(col)
    for s in sources:
        t.add_row(s["name"], s["vendor"], s["model"], str(s["is_hardware"]),
                  str(s["is_quantum"]), str(s["attested"]), str(s.get("device_path") or "-"))
    console.print(t)


@quw_app.command("healthcheck")
def healthcheck(
    n_bytes: int = typer.Option(8192, "--bytes", help="sample size to evaluate"),
    json_output: bool = typer.Option(False, "--json"),
):
    """Read a batch from the best source and run the SP 800-90B health battery."""
    from randomness.qrng import providers, health
    src = providers.auto_select(health_gated=False)
    data = src.random_bytes(n_bytes)
    rep = health.evaluate(data)
    out = {"source": src.info().as_dict(), **rep.as_dict()}
    if json_output:
        console.print_json(json.dumps(out)); return
    color = "green" if rep.passed else "red"
    console.print(f"[bold {color}]health: {'PASS' if rep.passed else 'FAIL'}[/]  "
                  f"min-entropy={rep.min_entropy_per_byte:.3f} bits/byte  "
                  f"RCT {rep.rct_max_run}/{rep.rct_cutoff}  APT {rep.apt_max_count}/{rep.apt_cutoff}")
    if not rep.passed:
        console.print("[red]reasons:[/] " + "; ".join(rep.reasons))


@quw_app.command("selftest")
def selftest(
    n_bytes: int = typer.Option(4096, "--bytes"),
    json_output: bool = typer.Option(False, "--json"),
):
    """Full local build->verify->reward roundtrip (proves the lane works here)."""
    from animica.quantum.quw_worker import selftest as _selftest
    rep = _selftest(n_bytes=n_bytes)
    if json_output:
        console.print_json(json.dumps(rep)); return
    ok = rep["verified"]
    console.print(f"[bold {'green' if ok else 'red'}]verified={ok}[/]  attested={rep['attested']}  "
                  f"signer={rep['signer']}  source={rep['source']['name']}")
    console.print(f"min-entropy={rep['min_entropy_per_byte']} bits/byte  metrics={rep['metrics']}")
    if not rep["attested"]:
        console.print("[yellow]NOTE: non-attested (software/no-HSM). Install YubiHSM2/TPM + QRNG "
                      "for attested rewards.[/]")


@quw_app.command("status")
def status(
    rpc_url: Optional[str] = typer.Option(None, "--rpc-url", help="query a node; omit for local"),
    json_output: bool = typer.Option(False, "--json"),
):
    """Show QUW lane status (contributors, attested share, local sources)."""
    if rpc_url:
        import urllib.request
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "rand.getQuantumStatus",
                           "params": {}}).encode()
        req = urllib.request.Request(rpc_url, data=body, headers={"content-type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            st = json.loads(r.read().decode()).get("result", {})
    else:
        from randomness.qrng.service import get_service
        from randomness.qrng import providers
        st = get_service().status()
        st["local_sources"] = [s.info().as_dict() for s in providers.detect_sources()]
    console.print_json(json.dumps(st))


@quw_app.command("credits")
def credits(address: str = typer.Argument(...), rpc_url: Optional[str] = typer.Option(None, "--rpc-url")):
    """Show quantum useful-work credit units earned by an address."""
    if rpc_url:
        import urllib.request
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "rand.getQuantumCredits",
                           "params": {"address": address}}).encode()
        req = urllib.request.Request(rpc_url, data=body, headers={"content-type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            out = json.loads(r.read().decode()).get("result", {})
    else:
        from randomness.qrng.service import get_service
        out = get_service().get_credits(address)
    console.print_json(json.dumps(out))


@quw_app.command("run")
def run(
    address: str = typer.Option(..., "--address", help="contributor wallet address"),
    rpc_url: Optional[str] = typer.Option(None, "--rpc-url", help="node RPC; omit for local in-process"),
    rounds: int = typer.Option(1, "--rounds", help="number of rounds to contribute (0=forever)"),
    round_id: Optional[int] = typer.Option(None, "--round", help="explicit round id (else time-based)"),
    interval: float = typer.Option(30.0, "--interval", help="seconds between rounds"),
    bytes_per: int = typer.Option(4096, "--bytes"),
    signer: Optional[str] = typer.Option(None, "--signer", help="yubihsm2|tpm2|software"),
    json_output: bool = typer.Option(False, "--json"),
):
    """Run the QUW contribution worker."""
    from animica.quantum.quw_worker import QuwWorker
    import time as _time
    w = QuwWorker(address=address, rpc_url=rpc_url, signer_prefer=signer, n_bytes=bytes_per)
    console.print(f"[cyan]QUW worker[/] source={w.source.info().name} signer={w.signer.info().backend} "
                  f"attested={w.signer.info().attested}")
    n = 0
    base = round_id if round_id is not None else int(_time.time() // 60)
    while rounds == 0 or n < rounds:
        rid = (round_id if round_id is not None else int(_time.time() // 60))
        out = w.run_once(rid)
        if json_output:
            console.print_json(json.dumps(out))
        else:
            r = out.get("result", {})
            console.print(f"round {rid}: submitted={out.get('submitted')} "
                          f"accepted={r.get('accepted')} attested={r.get('attested')} "
                          f"mixed={r.get('mixed')} units={r.get('credited_units')} {r.get('reason','')}")
        n += 1
        if rounds != 0 and n >= rounds:
            break
        _time.sleep(interval)


@quw_app.command("mode")
def mode(json_output: bool = typer.Option(False, "--json")):
    """Show the entropy source mode (pseudo-quantum vs real provider) + flip history."""
    from randomness.qrng.manager import get_manager
    st = get_manager().refresh()
    if json_output:
        console.print_json(json.dumps(st)); return
    color = "green" if st["real_available"] else "yellow"
    console.print(f"[bold {color}]mode={st['mode']}[/]  real_provider={st['real_available']}  "
                  f"attested={st['attested']}  source={st['active_source']['name']}")
    if st["flips"]:
        console.print("flips:")
        for f in st["flips"][-5:]:
            console.print(f"  {f['from_source']} -> {f['to_source']} ({f['reason']})")
    if not st["real_available"]:
        console.print("[yellow]serving PSEUDO-quantum — will auto-flip when a real QRNG connects "
                      "(plug in an IDQ Quantis, or `animica quantum quw connect --url ...`).[/]")


@quw_app.command("connect")
def connect(url: str = typer.Option(..., "--url", help="network QRNG endpoint (returns raw bytes)"),
            is_quantum: bool = typer.Option(True, "--quantum/--no-quantum"),
            model: str = typer.Option("network QRNG", "--model"),
            json_output: bool = typer.Option(False, "--json")):
    """Connect a real QRNG provider at runtime and flip the lane to it."""
    from randomness.qrng import providers
    from randomness.qrng.manager import get_manager
    src = providers.NetworkQRNG(url, is_quantum=is_quantum, model=model)
    st = get_manager().connect_provider(src, name="network-qrng")
    if json_output:
        console.print_json(json.dumps(st)); return
    console.print(f"[green]connected[/] {url} -> mode={st['mode']} real_provider={st['real_available']}")


@quw_app.command("simulate")
def simulate(json_output: bool = typer.Option(False, "--json")):
    """Disconnect runtime providers and serve pseudo-quantum (testing/degraded)."""
    from randomness.qrng.manager import get_manager
    st = get_manager().disconnect_all()
    if json_output:
        console.print_json(json.dumps(st)); return
    console.print(f"[yellow]serving pseudo-quantum[/] mode={st['mode']}")
