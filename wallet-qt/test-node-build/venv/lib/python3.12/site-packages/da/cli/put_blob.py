from __future__ import annotations

import argparse
import json
import os
import sys
from typing import BinaryIO, Optional

# Prefer the typed client if present
try:
    from da.retrieval.client import DAClient  # type: ignore
except Exception as _e:  # pragma: no cover
    DAClient = None  # type: ignore[assignment]


def _to_hex(x: object) -> str:
    if isinstance(x, (bytes, bytearray)):
        return "0x" + bytes(x).hex()
    if isinstance(x, str):
        return x if x.startswith("0x") else "0x" + x
    raise TypeError(f"cannot hex-encode object of type {type(x)!r}")


def _read_all(fp: BinaryIO) -> bytes:
    # Read stdin/file fully (CLI convenience). For very large blobs, prefer DAClient streaming.
    return fp.buffer.read() if fp is sys.stdin else fp.read()


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Animica • DA • put blob — post a blob and print commitment & receipt"
    )
    p.add_argument(
        "--url",
        default=os.environ.get("DA_URL", "http://127.0.0.1:8082"),
        help="DA service base URL (default: %(default)s or $DA_URL)",
    )
    p.add_argument(
        "--ns",
        type=int,
        required=True,
        help="namespace id (uint32, e.g. 24)",
    )
    p.add_argument(
        "--mime",
        default=None,
        help="MIME type hint, e.g. application/octet-stream",
    )
    p.add_argument(
        "--name",
        default=None,
        help="Optional logical name/filename to store alongside metadata",
    )
    p.add_argument(
        "--timeout",
        type=float,
        default=float(os.environ.get("DA_TIMEOUT", "30")),
        help="HTTP timeout seconds (default: %(default)s)",
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="Print JSON output (machine-readable)",
    )
    p.add_argument(
        "file",
        help="Path to file to upload, or '-' to read from stdin",
    )
    return p.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)

    if args.ns < 0 or args.ns > 0xFFFFFFFF:
        print("error: --ns must be uint32 (0..2^32-1)", file=sys.stderr)
        return 2

    # Obtain bytes or a file-like for streaming
    data: Optional[bytes] = None
    fp: Optional[BinaryIO] = None

    try:
        if args.file == "-":
            data = _read_all(sys.stdin.buffer)  # type: ignore[arg-type]
            size_hint = len(data)
            fname = args.name or "stdin.bin"
        else:
            fp = open(args.file, "rb")
            # If we have a client with streaming, we will pass fp; otherwise read all.
            fname = args.name or os.path.basename(args.file)
            size_hint = os.path.getsize(args.file)
    except FileNotFoundError:
        print(f"error: file not found: {args.file}", file=sys.stderr)
        return 2

    # Fast path: use DAClient if available
    if DAClient is not None:
        client = DAClient(base_url=args.url, timeout=args.timeout)  # type: ignore[call-arg]
        try:
            if fp is not None and hasattr(client, "post_blob_stream"):
                # Prefer streaming if the client supports it
                resp = client.post_blob_stream(  # type: ignore[attr-defined]
                    namespace=args.ns,
                    fileobj=fp,
                    mime=args.mime,
                    name=fname,
                    size=size_hint,
                )
            elif fp is not None and hasattr(client, "post_blob_file"):
                resp = client.post_blob_file(  # type: ignore[attr-defined]
                    namespace=args.ns,
                    path=args.file,
                    mime=args.mime,
                    name=fname,
                )
            else:
                # Fallback: bytes
                payload = data if data is not None else (fp.read() if fp else b"")
                resp = client.post_blob(  # type: ignore[attr-defined]
                    namespace=args.ns,
                    data=payload,
                    mime=args.mime,
                    name=fname,
                )

        finally:
            if fp is not None:
                fp.close()

        # Normalize response
        commitment = (
            resp.get("commitment")
            if isinstance(resp, dict)
            else getattr(resp, "commitment", None)
        )
        receipt = (
            resp.get("receipt")
            if isinstance(resp, dict)
            else getattr(resp, "receipt", None)
        )
        size = (
            resp.get("size", size_hint)
            if isinstance(resp, dict)
            else getattr(resp, "size", size_hint)
        )
        namespace = (
            resp.get("namespace", args.ns)
            if isinstance(resp, dict)
            else getattr(resp, "namespace", args.ns)
        )

        out = {
            "namespace": int(namespace),
            "size": int(size),
            "commitment": _to_hex(commitment),
            "receipt": receipt,
            "service_url": args.url,
            "name": fname,
            "mime": args.mime,
        }

        if args.json:
            print(json.dumps(out, indent=2))
        else:
            print(f"Namespace : {out['namespace']}")
            print(f"Size      : {out['size']} bytes")
            print(f"Commitment: {out['commitment']}")
            if receipt is not None:
                # Keep this brief; users can pass --json to see full receipt
                rid = receipt.get("id") if isinstance(receipt, dict) else None
                print(f"Receipt   : {rid or '(present)'}")
            print(f"Service   : {out['service_url']}")
            if fname:
                print(f"Name      : {fname}")
            if args.mime:
                print(f"MIME      : {args.mime}")

        return 0

    # Slow path: minimal HTTP client without da.retrieval.client dependency
    # Uses 'requests' if available; otherwise falls back to reading all bytes and urllib.
    payload_bytes: bytes
    if data is not None:
        payload_bytes = data
    elif fp is not None:
        try:
            payload_bytes = fp.read()
        finally:
            fp.close()
    else:
        payload_bytes = b""

    try:
        import requests  # type: ignore
    except Exception:  # pragma: no cover
        # urllib fallback
        from urllib import parse, request

        url = args.url.rstrip("/") + "/da/blob"
        req = request.Request(url, method="POST")
        req.add_header("Content-Type", "application/octet-stream")
        req.add_header("X-Animica-Namespace", str(args.ns))
        if args.mime:
            req.add_header("X-Animica-Mime", args.mime)
        if fname:
            req.add_header("X-Animica-Name", fname)
        try:
            with request.urlopen(req, data=payload_bytes, timeout=args.timeout) as r:
                body = r.read()
        except Exception as e:
            print(f"error: upload failed: {e}", file=sys.stderr)
            return 1
        try:
            resp = json.loads(body.decode("utf-8"))
        except Exception:
            print(body.decode("utf-8", "replace"))
            return 0
    else:
        url = args.url.rstrip("/") + "/da/blob"
        headers = {
            "Content-Type": "application/octet-stream",
            "X-Animica-Namespace": str(args.ns),
        }
        if args.mime:
            headers["X-Animica-Mime"] = args.mime
        if fname:
            headers["X-Animica-Name"] = fname
        try:
            r = requests.post(
                url, data=payload_bytes, headers=headers, timeout=args.timeout
            )
            r.raise_for_status()
            resp = r.json()
        except Exception as e:
            print(f"error: upload failed: {e}", file=sys.stderr)
            return 1

    # Normalize fallback response and print
    commitment = resp.get("commitment")
    size = resp.get("size", len(payload_bytes))
    receipt = resp.get("receipt")
    out = {
        "namespace": int(args.ns),
        "size": int(size),
        "commitment": _to_hex(commitment),
        "receipt": receipt,
        "service_url": args.url,
        "name": fname,
        "mime": args.mime,
    }
    if args.json:
        print(json.dumps(out, indent=2))
    else:
        print(f"Namespace : {out['namespace']}")
        print(f"Size      : {out['size']} bytes")
        print(f"Commitment: {out['commitment']}")
        if receipt is not None:
            rid = receipt.get("id") if isinstance(receipt, dict) else None
            print(f"Receipt   : {rid or '(present)'}")
        print(f"Service   : {out['service_url']}")
        if fname:
            print(f"Name      : {fname}")
        if args.mime:
            print(f"MIME      : {args.mime}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
