"""Bitcoin-Core-compatible control RPCs: stop, help."""

from __future__ import annotations

import os
from typing import Optional

from rpc.methods import method

_BTC_METHODS = {
    "Blockchain": ["getblockcount", "getbestblockhash", "getblockhash", "getblock",
                   "getblockheader", "getblockchaininfo", "getchaintips", "getdifficulty",
                   "getmempoolinfo", "getrawmempool"],
    "Rawtransactions": ["getrawtransaction", "sendrawtransaction", "testmempoolaccept",
                        "decoderawtransaction"],
    "Util": ["validateaddress"],
    "Network": ["getnetworkinfo", "getpeerinfo", "uptime"],
    "Wallet": ["getnewaddress", "getbalance", "listunspent", "sendtoaddress", "sendmany",
               "listtransactions", "gettransaction", "createwallet", "loadwallet",
               "listwallets", "backupwallet"],
    "Mining": ["getblocktemplate", "submitblock", "prioritisetransaction",
               "generatetoaddress", "getmininginfo"],
    "Control": ["stop", "help"],
}


@method("help", desc="Bitcoin-compat: list available commands.")
def help(command: Optional[str] = None) -> str:
    if command:
        for cat, names in _BTC_METHODS.items():
            if command in names:
                return f"{command}\n\nAnimica Bitcoin-Core-compatible RPC ({cat})."
        return f"help: unknown command: {command}"
    lines = ["== Animica Bitcoin-Core-Compatible RPC Mode =="]
    for cat, names in _BTC_METHODS.items():
        lines.append(f"\n== {cat} ==")
        lines.extend(names)
    return "\n".join(lines)


@method("stop", desc="Bitcoin-compat: stop the node (disabled by default).")
def stop(ctx=None) -> str:
    # This node may be the authoritative verifier seed; never kill it on an RPC
    # call unless explicitly allowed. Bitcoin clients accept the string + hang up.
    if os.environ.get("ANIMICA_BTC_COMPAT_ALLOW_STOP") == "1":
        try:
            import signal
            os.kill(os.getpid(), signal.SIGTERM)
        except Exception:
            pass
    return "Animica server stopping"
