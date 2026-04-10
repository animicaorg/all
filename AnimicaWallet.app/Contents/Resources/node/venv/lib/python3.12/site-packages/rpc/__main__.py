"""
RPC server entrypoint for `python -m rpc`.

This module allows starting the RPC server using:
    python -m rpc

It delegates to rpc.server:main() which configures and starts
the FastAPI/uvicorn server.
"""

from rpc.server import main

if __name__ == "__main__":
    main()
