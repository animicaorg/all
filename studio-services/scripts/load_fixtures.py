#!/usr/bin/env python3
"""
Load studio-services fixtures into a running studio-services instance:
 - Stores the Counter sample as an artifact
 - Relays the signed deploy tx from fixtures
 - Submits a verification job for the deployed contract (using the tx hash)
 - Waits for verification to complete and prints the result

Defaults:
  SERVICES_URL = http://localhost:8080
  API key (optional): env STUDIO_API_KEY (sent as Authorization: Bearer ...)

Usage:
  python studio-services/scripts/load_fixtures.py \
      [--services http://localhost:8080] \
      [--fixtures-dir studio-services/fixtures/counter] \
      [--no-deploy] [--no-verify] [--timeout 60]

Notes:
- The studio-services app must be running and configured to reach a devnet node.
- If --no-deploy is provided, you can pass --tx-hash to verify an already-mined tx.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import requests


def read_fixture_files(fixtures_dir: Path) -> Tuple[str, Dict[str, Any], bytes]:
    src = (fixtures_dir / "contract.py").read_text(encoding="utf-8")
    manifest = json.loads((fixtures_dir / "manifest.json").read_text(encoding="utf-8"))
    signed_tx_cbor = (fixtures_dir / "deploy_signed_tx.cbor").read_bytes()
    return src, manifest, signed_tx_cbor


def auth_headers() -> Dict[str, str]:
    key = os.getenv("STUDIO_API_KEY", "").strip()
    return {"Authorization": f"Bearer {key}"} if key else {}


def post_json(url: str, payload: Dict[str, Any], **kwargs) -> requests.Response:
    headers = {"content-type": "application/json", **auth_headers(), **kwargs.pop("headers", {})}
    return requests.post(url, data=json.dumps(payload), headers=headers, **kwargs)


def post_octet(url: str, blob: bytes, **kwargs) -> requests.Response:
    headers = {"content-type": "application/octet-stream", **auth_headers(), **kwargs.pop("headers", {})}
    return requests.post(url, data=blob, headers=headers, **kwargs)


def ensure_ok(resp: requests.Response, what: str) -> Dict[str, Any]:
    if resp.status_code // 100 != 2:
        try:
            body = resp.json()
        except Exception:
            body = resp.text
        raise SystemExit(f"{what} failed: HTTP {resp.status_code} — {body}")
    try:
        return resp.json()
    except Exception:
        return {"ok": True}


def store_artifact(services: str, source: str, manifest: Dict[str, Any]) -> str:
    """
    POST /artifacts with a simple JSON payload.
    The server computes content hash and returns an artifact id.
    """
    url = f"{services.rstrip('/')}/artifacts"
    payload = {
        "source": source,
        "manifest": manifest,
        # convenience: include ABI if present (many routes will extract from manifest)
        "abi": manifest.get("abi"),
        # attach a lightweight preview blob (base64) to help UIs; server may ignore
        "preview": {
            "filename": "contract.py",
            "encoding": "base64",
            "content": base64.b64encode(source.encode("utf-8")).decode("ascii"),
        },
    }
    data = ensure_ok(post_json(url, payload), "Artifact upload")
    artifact_id = data.get("id") or data.get("artifactId") or data.get("artifact_id")
    if not artifact_id:
        # fall back to printing the entire response for visibility
        print("Artifact upload response:", json.dumps(data, indent=2))
        raise SystemExit("Server response missing artifact id")
    return artifact_id


def relay_deploy_tx(services: str, signed_cbor: bytes) -> str:
    """
    POST /deploy with the already-signed CBOR tx (octet-stream). Returns tx hash.
    """
    url = f"{services.rstrip('/')}/deploy"
    data = ensure_ok(post_octet(url, signed_cbor), "Deploy relay")
    tx_hash = data.get("txHash") or data.get("tx_hash") or data.get("hash")
    if not tx_hash:
        print("Deploy response:", json.dumps(data, indent=2))
        raise SystemExit("Server response missing tx hash")
    return tx_hash


def submit_verify_job(services: str, source: str, manifest: Dict[str, Any], tx_hash: Optional[str], address: Optional[str]) -> str:
    """
    POST /verify with source+manifest and either txHash or address.
    Returns a job id to poll.
    """
    if not (tx_hash or address):
        raise ValueError("submit_verify_job requires tx_hash or address")

    url = f"{services.rstrip('/')}/verify"
    payload: Dict[str, Any] = {
        "source": source,
        "manifest": manifest,
    }
    if tx_hash:
        payload["txHash"] = tx_hash
    if address:
        payload["address"] = address

    data = ensure_ok(post_json(url, payload), "Verify submit")
    job_id = data.get("jobId") or data.get("job_id") or data.get("id")
    if not job_id:
        print("Verify submit response:", json.dumps(data, indent=2))
        raise SystemExit("Server response missing job id")
    return job_id


def poll_verify(services: str, job_id: Optional[str], tx_hash: Optional[str], timeout_s: int) -> Dict[str, Any]:
    """
    Poll GET /verify/{jobId} (preferred), or fallback to /verify/{txHash} until status is terminal.
    Terminal statuses: success | failed.
    """
    t0 = time.time()
    last = None
    while time.time() - t0 < timeout_s:
        if job_id:
            url = f"{services.rstrip('/')}/verify/{job_id}"
        elif tx_hash:
            url = f"{services.rstrip('/')}/verify/{tx_hash}"
        else:
            raise ValueError("poll_verify requires job_id or tx_hash")

        try:
            resp = requests.get(url, headers=auth_headers(), timeout=10)
            data = ensure_ok(resp, "Verify poll")
        except Exception as e:
            # transient? wait and retry
            last = {"error": str(e)}
            time.sleep(1.0)
            continue

        status = (data.get("status") or "").lower()
        last = data
        if status in ("success", "failed"):
            return data
        time.sleep(1.0)

    # timeout
    raise SystemExit(f"Verification did not complete within {timeout_s}s; last={json.dumps(last, indent=2)}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Load sample artifacts & verify the Counter contract")
    ap.add_argument("--services", default=os.getenv("SERVICES_URL", "http://localhost:8080"),
                    help="Base URL for studio-services (default: %(default)s)")
    ap.add_argument("--fixtures-dir", default="studio-services/fixtures/counter",
                    help="Directory containing contract.py, manifest.json, deploy_signed_tx.cbor")
    ap.add_argument("--no-deploy", action="store_true", help="Skip relaying the signed deploy tx")
    ap.add_argument("--no-verify", action="store_true", help="Skip verification")
    ap.add_argument("--tx-hash", default=None, help="Use an existing tx hash for verification (implies --no-deploy)")
    ap.add_argument("--address", default=None, help="Verify an already-deployed address (alternative to --tx-hash)")
    ap.add_argument("--timeout", type=int, default=60, help="Seconds to wait for verification to complete")
    args = ap.parse_args()

    services = args.services
    fixtures_dir = Path(args.fixtures_dir)
    if not fixtures_dir.exists():
        raise SystemExit(f"Fixtures directory not found: {fixtures_dir}")

    src, manifest, signed_cbor = read_fixture_files(fixtures_dir)

    print(f"→ Uploading artifact from {fixtures_dir} …")
    artifact_id = store_artifact(services, src, manifest)
    print(f"✓ Artifact stored: {artifact_id}")

    tx_hash: Optional[str] = None
    if args.tx_hash:
        tx_hash = args.tx_hash
        print(f"→ Using provided tx hash: {tx_hash} (skipping deploy)")
    elif not args.no_deploy:
        print("→ Relaying signed deploy tx …")
        tx_hash = relay_deploy_tx(services, signed_cbor)
        print(f"✓ Deploy relayed: txHash={tx_hash}")

    if args.no_verify:
        print("→ Skipping verification as requested (--no-verify). Done.")
        return

    job_id: Optional[str] = None
    if tx_hash or args.address:
        print("→ Submitting verification job …")
        job_id = submit_verify_job(services, src, manifest, tx_hash=tx_hash, address=args.address)
        print(f"✓ Verify job submitted: jobId={job_id}")
        print("→ Waiting for verification result …")
        result = poll_verify(services, job_id=job_id, tx_hash=tx_hash, timeout_s=args.timeout)
        status = result.get("status", "").upper()
        addr = result.get("address") or result.get("contractAddress")
        code_hash = result.get("codeHash") or result.get("code_hash")
        print("═ Verification Result ═")
        print(json.dumps(result, indent=2))
        if status != "SUCCESS":
            raise SystemExit("Verification failed (see result above).")
        print(f"✓ Verified OK: address={addr} codeHash={code_hash}")
    else:
        print("→ No tx hash or address available; skipping verification.\n"
              "   You can run again with --tx-hash <hash> or --address <addr> to verify.")

    print("All done.")
    print("\nTips:")
    print(" - Set STUDIO_API_KEY if your instance requires auth.")
    print(" - Override SERVICES_URL env var or use --services to target a remote instance.")
    print(" - Use --no-deploy with --tx-hash to verify an already-mined contract.")
    print(" - Logs include full JSON responses for troubleshooting.")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        sys.exit(130)
