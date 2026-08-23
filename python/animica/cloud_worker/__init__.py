"""Animica Python Cloud — compute provider worker.

Turns a machine with Docker into a compute provider for the Animica Python Cloud fleet:

    pip install -U animica
    python -m animica.cloud_worker build-image --gateway https://animica.dev
    python -m animica.cloud_worker run --gateway https://animica.dev --address anim1...

The worker self-registers (bearer token generated locally, gateway stores only its sha3-256),
long-polls the dispatch queue, runs each claimed job inside the SAME hardened Docker sandbox
contract the gateway itself uses (``--network none``, read-only rootfs, all capabilities
dropped, memory/CPU/pid caps, unprivileged user), and posts the result back. Payment is a
share of the customer's ANM payment for that very execution, credited to the registered
payout address as real, spendable ledger balance the moment the result settles — not an IOU.

Hard rule: jobs are UNTRUSTED code. The worker checks for Docker and the
``anm-pycloud-runtime`` image at startup and refuses to run without them. There is no
unsandboxed fallback, by design.
"""

from .worker import (  # noqa: F401
    DEFAULT_GATEWAY,
    RUNTIME_IMAGE,
    GatewayClient,
    SandboxUnavailable,
    Worker,
    build_image,
    check_docker,
    check_image,
    run_job_in_sandbox,
)

__all__ = [
    "DEFAULT_GATEWAY",
    "RUNTIME_IMAGE",
    "GatewayClient",
    "SandboxUnavailable",
    "Worker",
    "build_image",
    "check_docker",
    "check_image",
    "run_job_in_sandbox",
]
