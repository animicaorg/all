"""CLI for the Animica Python Cloud provider worker.

    python -m animica.cloud_worker build-image --gateway https://animica.dev
    python -m animica.cloud_worker run --gateway https://animica.dev --address anim1...
    python -m animica.cloud_worker status --gateway https://animica.dev
"""

from __future__ import annotations

import argparse
import json
import signal
import sys

from .worker import (
    DEFAULT_GATEWAY,
    DOCKER_BIN,
    RUNTIME_IMAGE,
    GatewayClient,
    SandboxUnavailable,
    Worker,
    build_image,
    check_docker,
    check_image,
    load_state,
)


def _add_common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--gateway", default=DEFAULT_GATEWAY, help=f"gateway base URL (default {DEFAULT_GATEWAY})")
    p.add_argument("--image", default=RUNTIME_IMAGE, help=f"sandbox image tag (default {RUNTIME_IMAGE})")
    p.add_argument("--docker-bin", default=DOCKER_BIN, help="docker binary")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="animica.cloud_worker",
                                     description="Serve Animica Python Cloud jobs and earn ANM.")
    sub = parser.add_subparsers(dest="cmd")

    run_p = sub.add_parser("run", help="register and serve jobs (the default)")
    _add_common(run_p)
    run_p.add_argument("--address", required=True, help="bech32m anim1... payout address (earnings land here)")
    run_p.add_argument("--name", default="", help="a label for this machine")
    run_p.add_argument("--gpu", default=None, help="advertise a GPU (auto-detected via nvidia-smi when omitted)")
    run_p.add_argument("--cpus", default="1", help="CPUs per job container (default 1)")
    run_p.add_argument("--poll", type=float, default=5.0, help="idle poll interval, seconds")
    run_p.add_argument("--once", action="store_true", help="serve exactly one job, then exit")

    build_p = sub.add_parser("build-image", help="build the exact sandbox image the gateway runs")
    _add_common(build_p)

    status_p = sub.add_parser("status", help="check docker, image and network stats")
    _add_common(status_p)

    args = parser.parse_args(argv)
    if not args.cmd:
        parser.print_help()
        print("\nhint: start with  python -m animica.cloud_worker run --gateway "
              f"{DEFAULT_GATEWAY} --address anim1...", file=sys.stderr)
        return 2

    try:
        if args.cmd == "build-image":
            tag = build_image(args.gateway, image=args.image, docker_bin=args.docker_bin)
            print(f"[worker] built {tag}. Next:\n  python -m animica.cloud_worker run "
                  f"--gateway {args.gateway} --address anim1YOUR_PAYOUT_ADDRESS")
            return 0

        if args.cmd == "status":
            try:
                version = check_docker(args.docker_bin)
                print(f"docker: OK (server {version})")
            except SandboxUnavailable as exc:
                print(f"docker: MISSING — {exc}")
                version = None
            has_image = bool(version) and check_image(args.image, args.docker_bin)
            print(f"image {args.image}: {'present' if has_image else 'MISSING (run build-image)'}")
            state = load_state()
            if state.get("provider_id"):
                print(f"registered: provider {state['provider_id']} -> {state.get('address')} @ {state.get('gateway')}")
            else:
                print("registered: not yet")
            stats = GatewayClient(args.gateway)._request("GET", "/api/cloud/v1/providers")  # noqa: SLF001
            print("network: " + json.dumps(stats, indent=2))
            return 0

        # run
        worker = Worker(gateway=args.gateway, address=args.address, name=args.name,
                        gpu=args.gpu, image=args.image, docker_bin=args.docker_bin,
                        poll_seconds=args.poll, cpus=args.cpus)

        def _sig(_s, _f):
            print("\n[worker] stopping after the current job...")
            worker.stop()

        signal.signal(signal.SIGINT, _sig)
        signal.signal(signal.SIGTERM, _sig)
        worker.run(once=args.once)
        return 0

    except SandboxUnavailable as exc:
        print(f"REFUSING TO RUN: {exc}", file=sys.stderr)
        return 3
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
