from __future__ import annotations

import uvicorn

from .api import create_app
from .config import load_config


def run() -> None:
    cfg = load_config()
    app = create_app(cfg)
    uvicorn.run(app, host=cfg.api_host, port=cfg.api_port, log_level="info")


if __name__ == "__main__":
    run()

