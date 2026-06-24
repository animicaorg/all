"""`animica-node` launcher.

Thin wrapper around the node's RPC server entrypoint — identical to
``python -m rpc``, but installed as a friendly console command and shipped by
the slim, GPU-free `animica-node` package. Configuration is read from
``ANIMICA_*`` environment variables (see --help).
"""

from __future__ import annotations

import os
import sys

from . import __version__

_BANNER = f"""animica-node {__version__} — slim Animica node (no GPU/AI stack)

Starts a full node + JSON-RPC server. Configure with environment variables:

  ANIMICA_NETWORK       network to join (e.g. mainnet)             [default: mainnet]
  ANIMICA_DATA_DIR      data directory                            [default: ~/.animica]
  ANIMICA_RPC_HOST      RPC bind host                             [default: 127.0.0.1]
  ANIMICA_RPC_PORT      RPC bind port                             [default: 8545]
  ANIMICA_P2P_ENABLE    join the p2p network (true/false)         [default: true]
  ANIMICA_LOG_LEVEL     log level (INFO/DEBUG/WARNING)            [default: INFO]

JSON-RPC is served at:
  POST /rpc   native namespaces: state.*  chain.*  tx.*  mempool.*  p2p.*  node.*
  POST /      Bitcoin Core-compatible (getblockchaininfo, sendrawtransaction, …)
  POST /      Ethereum-compatible      (eth_chainId, eth_getBalance, …; chain id 149)

Docs: https://animica.org/docs   ·   Bitcoin/EVM RPC: /docs/bitcoin-rpc, /docs/evm-rpc
"""


def main() -> None:
    argv = sys.argv[1:]
    if any(a in ("-h", "--help") for a in argv):
        sys.stdout.write(_BANNER)
        return
    if any(a in ("-V", "--version") for a in argv):
        sys.stdout.write(f"animica-node {__version__}\n")
        return

    # Default to mainnet so an exchange operator can `animica-node` with no flags.
    os.environ.setdefault("ANIMICA_NETWORK", "mainnet")

    try:
        from rpc.server import main as _serve
    except Exception as exc:  # pragma: no cover - surfaced only on a broken install
        sys.stderr.write(
            "animica-node: failed to import the node RPC server (rpc.server). "
            f"The install may be incomplete: {exc}\n"
        )
        raise SystemExit(1)

    _serve()


if __name__ == "__main__":
    main()
