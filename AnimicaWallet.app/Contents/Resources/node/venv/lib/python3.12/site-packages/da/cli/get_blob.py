from __future__ import annotations

import argparse
import os
import sys
from typing import BinaryIO, Dict, Optional

# Prefer the typed client if present
try:
    from da.retrieval.client import DAClient  # type: ignore
except Exception:
    DAClient = None  # type: ignore


def _normalize_commit_hex(s: str) -> str:
    """
    Normalize a 32-byte (64-hex) commitment string to 0x-prefixed lowercase hex.
    """
    s = s.strip().lower()
    if s.startswith("0x"):
        s = s[2:]
    if len(s) != 64:
        raise ValueError("commitment must be 32 bytes (64 hex chars)")
    int(s, 16)  # validate hex
    return "0x" + s


def _stderr(msg: str) -> None:
    print(msg, file=sys.stderr)


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Animica • DA • get blob — fetch a blob by commitment"
    )
    p.add_argument(
        "--url",
        default=os.environ.get("DA_URL", "http://127.0.0.1:8082"),
        help="DA service base URL (default: %(default)s or $DA_URL)",
    )
    p.add_argument(
        "--commit",
        required=True,
        help="blob commitment (32-byte NMT root) as hex, e.g. 0xabcd…",
    )
    p.add_argument(
        "--range-start",
        type=int,
        default=None,
        help="optional byte-range start (non-negative)",
    )
    p.add_argument(
        "--range-len",
        type=int,
        default=None,
        help="optional byte-range length (non-negative)",
    )
    p.add_argument(
        "--timeout",
        type=float,
        default=float(os.environ.get("DA_TIMEOUT", "30")),
        help="HTTP timeout seconds (default: %(default)s)",
    )
    p.add_argument(
        "--out",
        default="-",
        help="output path (default: '-' for stdout)",
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="emit a JSON summary to stderr after download",
    )
    return p.parse_args(argv)


def _open_out(path: str) -> BinaryIO:
    if path == "-" or path == "":
        return sys.stdout.buffer
    # ensure parent dirs exist
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    return open(path, "wb")


def _range_to_header(start: Optional[int], length: Optional[int]) -> Optional[str]:
    if (start is None) ^ (length is None):
        raise ValueError("--range-start and --range-len must be provided together")
    if start is None:
        return None
    if start < 0 or length < 0:
        raise ValueError("range values must be non-negative")
    end = start + length - 1 if length > 0 else start - 1  # zero-length disallowed
    if end < start:
        raise ValueError("range length must be > 0")
    return f"bytes={start}-{end}"


def _download_with_requests(
    url: str, range_header: Optional[str], timeout: float, out_fp: BinaryIO
) -> Dict[str, object]:
    import requests  # type: ignore

    headers = {}
    if range_header:
        headers["Range"] = range_header

    with requests.get(url, headers=headers, timeout=timeout, stream=True) as r:
        try:
            r.raise_for_status()
        except Exception as e:
            # Surface server text (often JSON) to stderr for easier debugging.
            _stderr(f"error: GET failed: {e}")
            try:
                _stderr(r.text)
            except Exception:
                pass
            raise

        # Determine sizes
        total_size: Optional[int] = None
        if r.status_code == 206 and "Content-Range" in r.headers:
            # Content-Range: bytes start-end/total
            cr = r.headers["Content-Range"]
            try:
                total_size = int(cr.split("/")[-1])
            except Exception:
                total_size = None
        elif "Content-Length" in r.headers:
            try:
                total_size = int(r.headers["Content-Length"])
            except Exception:
                total_size = None

        # Stream to out
        bytes_written = 0
        for chunk in r.iter_content(chunk_size=64 * 1024):
            if not chunk:
                continue
            out_fp.write(chunk)
            bytes_written += len(chunk)

        return {
            "status": r.status_code,
            "bytes": bytes_written,
            "total_size": total_size,
            "content_type": r.headers.get("Content-Type"),
        }


def _download_with_urllib(
    url: str, range_header: Optional[str], timeout: float, out_fp: BinaryIO
) -> Dict[str, object]:
    from urllib import request

    req = request.Request(url, method="GET")
    if range_header:
        req.add_header("Range", range_header)
    with request.urlopen(req, timeout=timeout) as resp:
        meta = dict(resp.headers.items())
        content_type = meta.get("Content-Type")
        content_length = meta.get("Content-Length")
        total_size = (
            int(content_length) if content_length and content_length.isdigit() else None
        )

        bytes_written = 0
        while True:
            chunk = resp.read(64 * 1024)
            if not chunk:
                break
            out_fp.write(chunk)
            bytes_written += len(chunk)

        return {
            "status": getattr(resp, "status", 200),
            "bytes": bytes_written,
            "total_size": total_size,
            "content_type": content_type,
        }


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)

    try:
        commit_hex = _normalize_commit_hex(args.commit)
    except Exception as e:
        _stderr(f"error: invalid --commit: {e}")
        return 2

    try:
        rng = _range_to_header(args.range_start, args.range_len)
    except Exception as e:
        _stderr(f"error: invalid range: {e}")
        return 2

    url = args.url.rstrip("/") + "/da/blob/" + commit_hex

    # If a typed client is available, try to use it first (streaming preferred).
    out_fp: Optional[BinaryIO] = None
    try:
        out_fp = _open_out(args.out)
    except Exception as e:
        _stderr(f"error: cannot open output: {e}")
        return 2

    summary: Dict[str, object]

    try:
        if DAClient is not None:
            client = DAClient(base_url=args.url, timeout=args.timeout)  # type: ignore[call-arg]
            # Prefer streaming if provided by the client
            if hasattr(client, "get_blob_stream"):
                it = client.get_blob_stream(commit_hex, range_start=args.range_start, range_len=args.range_len)  # type: ignore[attr-defined]
                bytes_written = 0
                for chunk in it:
                    out_fp.write(chunk)
                    bytes_written += len(chunk)
                summary = {
                    "status": 200,
                    "bytes": bytes_written,
                    "total_size": None,
                    "content_type": "application/octet-stream",
                }
            elif hasattr(client, "get_blob"):
                data = client.get_blob(commit_hex, range_start=args.range_start, range_len=args.range_len)  # type: ignore[attr-defined]
                if isinstance(data, (bytes, bytearray)):
                    out_fp.write(data)
                    summary = {
                        "status": 200,
                        "bytes": len(data),
                        "total_size": len(data),
                        "content_type": "application/octet-stream",
                    }
                else:
                    # Fallback to HTTP path if unexpected type
                    raise RuntimeError(
                        "DAClient.get_blob returned unexpected type; falling back to HTTP"
                    )
            else:
                raise RuntimeError(
                    "DAClient does not provide get_blob[_stream]; falling back to HTTP"
                )
        else:
            raise RuntimeError("DAClient unavailable; using HTTP")
    except Exception:
        # Raw HTTP fallback
        try:
            import requests  # type: ignore
        except Exception:
            summary = _download_with_urllib(url, rng, args.timeout, out_fp)
        else:
            summary = _download_with_requests(url, rng, args.timeout, out_fp)
    finally:
        # Only close the file if it's not stdout
        if args.out != "-" and out_fp and out_fp is not sys.stdout.buffer:
            out_fp.close()

    if args.json:
        # Emit JSON summary to STDERR to avoid corrupting stdout binary data.
        import json

        meta = {
            "url": url,
            "commitment": commit_hex,
            "range": (
                {"start": args.range_start, "len": args.range_len}
                if args.range_start is not None
                else None
            ),
            **summary,
        }
        _stderr(json.dumps(meta, indent=2))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
