#!/usr/bin/env python3
"""Regenerate https://animica.dev/openapi.json from live sources.

WHY THIS EXISTS. Some x402 crawlers (x402scan among them) discover an origin
by reading /openapi.json and IGNORE /.well-known/x402. On 2026-08-18 the root
spec listed a stale subset of 8 paid paths while 24 were live; removing them
by hand then left crawlers with ZERO paid resources to register. Neither
hand-maintained state is correct, so the file is GENERATED: the free API paths
are preserved verbatim and every paid path is copied from the gateway's own
OpenAPI, which is itself generated from the live product registry.

Re-run after adding or removing a product:
    python3 bin/regen-root-openapi.py
"""
import json
import shutil
import time
import urllib.request

ROOT = "/var/www/animica.dev/openapi.json"
X402 = "https://animica.dev/x402/openapi.json"


def fetch(url):
    req = urllib.request.Request(url, headers={"accept": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def main():
    root = json.load(open(ROOT))
    paid = fetch(X402)

    # Keep only the FREE paths from whatever is on disk, then re-add every paid
    # path from the generated spec. Anything stale disappears by construction.
    free = {k: v for k, v in root["paths"].items() if not k.startswith("/x402")}
    merged = dict(free)
    added = 0
    for path, item in paid.get("paths", {}).items():
        if not path.startswith("/x402"):
            continue
        # FREE routes must not be listed as paid. A crawler that probes them
        # gets 200 (or 405) and records "no valid x402 response" against us —
        # which is exactly the failure report that started this. Trials, the
        # commit reveal, notarisation verification, blob retrieval and the
        # credit balance are all free by design.
        if path in ("/x402", "/x402/openapi.json", "/x402/stats", "/x402/healthz",
                    "/.well-known/x402", "/x402/credits/balance"):
            continue
        if path.endswith("/trial"):
            continue
        if any(seg in path for seg in ("/random/reveal/", "/notarize/verify/", "/blob/{",
                                       "/forecast/calibration", "/forecast/{")):
            continue
        # The free crawler answers 200, not 402. Listing it as paid would make
        # every scanner record a failure against a working endpoint — the exact
        # defect that produced the original "no valid x402 response" report.
        if path == "/x402/crawl":
            continue
        merged[path] = item
        added += 1

    # Copy the components those paid paths REFERENCE. Without this every
    # $ref like #/components/parameters/PaymentSignature dangles, and a
    # consumer that resolves refs crashes on null instead of registering the
    # resource — observed verbatim from x402scan:
    #   "Cannot read properties of null (reading 'PaymentSignature')"
    # A spec that lists paths without their components is not a valid spec.
    root.setdefault("components", {})
    for section, entries in (paid.get("components") or {}).items():
        dest = root["components"].setdefault(section, {})
        for name, value in entries.items():
            dest.setdefault(name, value)

    root["paths"] = merged
    root["x-x402"] = {
        "note": "Paths under /x402 are PAID and answer 402; all others here are free.",
        "catalog": "https://animica.dev/.well-known/x402",
        "openapi": X402,
        "gateway": "https://animica.dev/x402",
        "payment_protocol": "x402",
        "networks": ["eip155:8453", "animica:1"],
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    root["externalDocs"] = {
        "description": "Paid agent APIs (x402) — generated live from the gateway registry",
        "url": X402,
    }

    shutil.copy(ROOT, ROOT + ".bak.regen")
    with open(ROOT, "w") as f:
        json.dump(root, f, indent=1)
    print(f"free paths: {len(free)}  paid paths: {added}  total: {len(merged)}")


if __name__ == "__main__":
    main()
