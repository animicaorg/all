"""animica-node — slim, no-GPU Animica node runtime.

Ships the node + JSON-RPC subsystems (chain, state, consensus, mempool, p2p,
DA, VM, post-quantum crypto, and the Bitcoin- and Ethereum-compatible RPC
facades) with NO torch/transformers/CUDA dependency. Run a node with the
``animica-node`` console script or ``python -m rpc``.
"""

__version__ = "4.0.1"
__all__ = ["__version__"]
