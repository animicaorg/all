"""
Animica DA • Serve CLI

Start provider service daemon to serve blobs over HTTP.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Optional

try:
    import uvicorn  # type: ignore
except ImportError:
    print("Error: uvicorn is required for serve command", file=sys.stderr)
    print("Install with: pip install 'uvicorn[standard]'", file=sys.stderr)
    sys.exit(1)

try:
    from da.provider.service import ProviderService
except ImportError as e:
    print(f"Error importing DA provider service: {e}", file=sys.stderr)
    sys.exit(1)


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    p = argparse.ArgumentParser(
        description="Animica DA • Serve — Start provider service daemon"
    )
    p.add_argument(
        "--path",
        type=Path,
        required=True,
        help="Storage path for blobs",
    )
    p.add_argument(
        "--port",
        type=int,
        default=9090,
        help="HTTP port to listen on (default: 9090)",
    )
    p.add_argument(
        "--host",
        default="0.0.0.0",
        help="Host to bind to (default: 0.0.0.0)",
    )
    p.add_argument(
        "--rate-limit",
        type=int,
        default=100,
        help="Requests per second rate limit (default: 100)",
    )
    p.add_argument(
        "--auth-token",
        default=None,
        help="Optional bearer token for authentication",
    )
    p.add_argument(
        "--workers",
        type=int,
        default=1,
        help="Number of worker processes (default: 1)",
    )
    p.add_argument(
        "--reload",
        action="store_true",
        help="Enable auto-reload on code changes (development)",
    )
    return p.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    """Main entry point for serve command."""
    args = parse_args(argv)

    # Validate storage path
    storage_path = Path(args.path)
    storage_path.mkdir(parents=True, exist_ok=True)

    # Create provider service
    try:
        service = ProviderService(
            storage_path=storage_path,
            rate_limit_rps=args.rate_limit,
            auth_token=args.auth_token,
        )
    except Exception as e:
        print(f"Error creating provider service: {e}", file=sys.stderr)
        return 1

    # Print startup info
    print("=" * 60)
    print("Animica DA Provider Service")
    print("=" * 60)
    print(f"Storage Path:  {storage_path.resolve()}")
    print(f"Listen Address: {args.host}:{args.port}")
    print(f"Rate Limit:    {args.rate_limit} req/s")
    if args.auth_token:
        print("Auth:          Enabled (Bearer token)")
    else:
        print("Auth:          Disabled (public)")
    print("=" * 60)
    print()
    print("Endpoints:")
    print(f"  GET  /blob/{{commitment}}  - Retrieve blob")
    print(f"  HEAD /blob/{{commitment}}  - Check if blob exists")
    print(f"  GET  /health              - Health check")
    print()
    print("Press Ctrl+C to stop")
    print("=" * 60)

    # Run uvicorn server
    try:
        uvicorn.run(
            service.app,
            host=args.host,
            port=args.port,
            workers=args.workers,
            reload=args.reload,
            log_level="info",
        )
    except KeyboardInterrupt:
        print("\nShutting down...")
        return 0
    except Exception as e:
        print(f"Error running server: {e}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
