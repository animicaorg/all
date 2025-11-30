from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from aicf.node import make_block

DEFAULT_RPC = "http://127.0.0.1:8545"


def _normalize_rpc_url(rpc_url: str) -> str:
    parsed = urlparse(rpc_url)
    path = parsed.path or ""
    if path.rstrip("/") == "/rpc":
        return rpc_url
    if path and path not in {"", "/"}:
        return rpc_url
    return rpc_url.rstrip("/") + "/rpc"


def _rpc_call(rpc_url: str, method: str, params: Optional[list[Any]] = None) -> Any:
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or []}
    data_bytes = json.dumps(payload).encode()

    errors: list[str] = []
    targets = []
    for candidate in (_normalize_rpc_url(rpc_url), rpc_url):
        if candidate not in targets:
            targets.append(candidate)

    for target in targets:  # normalize first, original URL as fallback
        try:
            req = Request(
                target,
                data=data_bytes,
                headers={
                    "Content-Type": "application/json",
                    "Content-Length": str(len(data_bytes)),
                    "Connection": "close",
                },
            )
            with urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode())
            break
        except (HTTPError, URLError, TimeoutError, ValueError) as exc:  # pragma: no cover - handled by caller
            errors.append(f"{target}: {exc}")
            continue
    else:  # pragma: no cover - loop always breaks or raises
        raise RuntimeError(f"RPC request failed: {'; '.join(errors)}")

    if not isinstance(data, dict):
        raise RuntimeError(f"Unexpected RPC response: {data}")
    if "error" in data:
        raise RuntimeError(json.dumps(data["error"]))
    return data.get("result")


def _parse_height(value: Any) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value, 16) if value.startswith("0x") else int(value)
        except Exception:
            pass
    raise RuntimeError(f"Unable to parse height from {value!r}")


def _load_local_state(datadir: Path) -> Dict[str, Any]:
    state_path = datadir / "state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    if state_path.exists():
        try:
            data = json.loads(state_path.read_text())
            return {
                "height": int(data.get("height", 0)),
                "chainId": data.get("chain_id", "0xa11ca"),
                "autoMine": bool(data.get("auto_mine", False)),
                "_path": state_path,
            }
        except Exception:
            pass
    return {"height": 0, "chainId": "0xa11ca", "autoMine": False, "_path": state_path}


def _write_local_state(state: Dict[str, Any]) -> None:
    path = state.get("_path")
    if isinstance(path, Path):
        path.write_text(
            json.dumps({"height": state["height"], "chain_id": state["chainId"], "auto_mine": state["autoMine"]})
        )


def _status(rpc_url: str, datadir: Optional[Path]) -> Dict[str, Any]:
    if datadir:
        state = _load_local_state(datadir)
        state.pop("_path", None)
        return state

    try:
        head = _rpc_call(rpc_url, "chain.getHead")
        if isinstance(head, dict):
            return {
                "height": _parse_height(head.get("height", head.get("number", 0))),
                "chainId": head.get("chainId"),
                "autoMine": bool(head.get("autoMine", False)),
            }
    except RuntimeError:
        pass
    try:
        info = _rpc_call(rpc_url, "animica_status")
        if isinstance(info, dict):
            info["height"] = _parse_height(info.get("height", 0))
            info.setdefault("autoMine", False)
            info.setdefault("chainId", None)
            return info
    except RuntimeError:
        pass
    height = _parse_height(_rpc_call(rpc_url, "eth_blockNumber"))
    chain_id = _rpc_call(rpc_url, "eth_chainId")
    return {"height": height, "chainId": chain_id, "autoMine": False}


def _mine(rpc_url: str, count: int, datadir: Optional[Path]) -> int:
    if datadir:
        state = _load_local_state(datadir)
        if count > 0:
            state["height"] += int(count)
            _write_local_state(state)
        return state["height"]
    if count <= 0:
        return _status(rpc_url, None)["height"]
    start_height = _status(rpc_url, None)["height"]
    current_height = start_height
    try:
        for _ in range(count):
            work = _rpc_call(rpc_url, "miner.getWork")
            payload: Dict[str, Any] = {"jobId": work.get("jobId") if isinstance(work, dict) else None}
            if isinstance(work, dict) and "header" in work:
                payload["header"] = work["header"]
            payload.setdefault("nonce", hex(int(time.time() * 1000) & 0xFFFFFFFF))
            _rpc_call(rpc_url, "miner.submit_sha256_block", payload)
        current_height = _status(rpc_url, None)["height"]
        if current_height > start_height:
            return current_height
    except RuntimeError:
        pass
    try:
        result = _rpc_call(rpc_url, "animica_generate", [count])
        if isinstance(result, dict) and "height" in result:
            return int(result["height"])
        current_height = _status(rpc_url, None)["height"]
        if current_height > start_height:
            return current_height
    except RuntimeError:
        pass
    try:
        evm_height = _parse_height(_rpc_call(rpc_url, "evm_mine", [count]))
        if evm_height > start_height:
            return evm_height
    except RuntimeError:
        pass

    try:
        _rpc_call(rpc_url, "anvil_mine", [count])
        current_height = _status(rpc_url, None)["height"]
        if current_height > start_height:
            return current_height
    except RuntimeError:
        pass

    try:
        return _status(rpc_url, None)["height"]
    except RuntimeError:
        return start_height


def _block(rpc_url: str, tag: str, datadir: Optional[Path]) -> Dict[str, Any]:
    if datadir:
        state = _load_local_state(datadir)
        if isinstance(tag, str):
            if tag in ("latest", "finalized", "safe", "pending"):
                n = state["height"]
            elif tag == "earliest":
                n = 0
            elif tag.startswith("0x"):
                n = int(tag, 16)
            else:
                n = int(tag)
        else:
            n = int(tag)
        return make_block(max(0, n))
    try:
        result = _rpc_call(rpc_url, "chain.getBlockByNumber", [tag, False, False])
        if isinstance(result, dict):
            return result
    except RuntimeError:
        pass
    try:
        result = _rpc_call(rpc_url, "animica_getBlock", [tag])
        if isinstance(result, dict):
            return result
    except RuntimeError:
        pass
    result = _rpc_call(rpc_url, "eth_getBlockByNumber", [tag, False])
    if not isinstance(result, dict):
        raise RuntimeError(f"Unexpected block payload: {result!r}")
    return result


def cmd_status(args: argparse.Namespace) -> None:
    info = _status(args.rpc_url, args.datadir)
    if args.json:
        print(json.dumps(info, indent=2))
    else:
        print(f"chainId={info['chainId']} height={info['height']} autoMine={info['autoMine']}")


def cmd_mine(args: argparse.Namespace) -> None:
    new_height = _mine(args.rpc_url, args.count, args.datadir)
    print(new_height)


def cmd_block(args: argparse.Namespace) -> None:
    blk = _block(args.rpc_url, args.tag, args.datadir)
    print(json.dumps(blk, indent=2) if args.json else blk)


def cmd_auto(args: argparse.Namespace) -> None:
    if args.datadir:
        state = _load_local_state(args.datadir)
        state["autoMine"] = bool(args.enable)
        _write_local_state(state)
        print("on" if state["autoMine"] else "off")
    else:
        method = "miner_start" if args.enable else "miner_stop"
        result = _rpc_call(args.rpc_url, method)
        print("on" if result else "off")


def cmd_pipeline(args: argparse.Namespace) -> None:
    start_info = _status(args.rpc_url, args.datadir)
    if args.wait:
        time.sleep(args.wait)
    height_after = _mine(args.rpc_url, args.mine, args.datadir)
    if args.wait:
        time.sleep(args.wait)
    head = _block(args.rpc_url, "latest", args.datadir)
    summary = {
        "startHeight": start_info["height"],
        "endHeight": height_after,
        "chainId": start_info["chainId"],
        "headHash": head.get("hash"),
    }
    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        print(
            f"startHeight={summary['startHeight']} endHeight={summary['endHeight']} "
            f"chainId={summary['chainId']} headHash={summary['headHash']}"
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Pipeline helper for Animica's bitcoin-style node shim")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--rpc-url", "-r", default=DEFAULT_RPC, help="JSON-RPC endpoint")
    common.add_argument("--datadir", "-d", type=Path, help="Operate directly on a local datadir (offline mode)")
    sub = parser.add_subparsers(dest="command", required=True)

    p_status = sub.add_parser("status", parents=[common], help="Show chain id and height")
    p_status.add_argument("--json", action="store_true", help="Emit JSON")
    p_status.set_defaults(func=cmd_status)

    p_mine = sub.add_parser("mine", parents=[common], help="Mine N blocks")
    p_mine.add_argument("--count", "-n", type=int, default=1, help="Blocks to mine")
    p_mine.set_defaults(func=cmd_mine)

    p_block = sub.add_parser("block", parents=[common], help="Fetch a block")
    p_block.add_argument("tag", nargs="?", default="latest", help="Block number or tag")
    p_block.add_argument("--json", action="store_true", help="Emit JSON")
    p_block.set_defaults(func=cmd_block)

    p_auto = sub.add_parser("auto", parents=[common], help="Toggle auto-mining")
    p_auto.add_argument("enable", type=str, help="true/false")
    p_auto.set_defaults(func=cmd_auto)

    p_pipeline = sub.add_parser("pipeline", parents=[common], help="Run status → mine → head fetch")
    p_pipeline.add_argument("--mine", "-m", type=int, default=1, help="Blocks to mine")
    p_pipeline.add_argument("--wait", type=float, default=0.2, help="Delay between stages")
    p_pipeline.add_argument("--json", action="store_true", help="Emit JSON")
    p_pipeline.set_defaults(func=cmd_pipeline)

    return parser


def main(argv: Optional[list[str]] = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "auto":
        args.enable = args.enable.lower() in ("1", "true", "yes", "on")
    try:
        args.func(args)
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
