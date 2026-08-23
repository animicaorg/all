"""Animica Python Cloud SDK — deploy Python functions to animica.dev.

```python
from animica.cloud import App

app = App("my-api")

@app.function(memory_mb=256, timeout=30, capabilities=["AI_INFERENCE"])
def hello(request):
    return {"hello": request.get("name")}
```

Deploy with ``animica cloud deploy`` (or drive :class:`CloudClient` directly). The honest
mechanics, stated once: the deployment is ANCHORED on Animica consensus (a DEPLOY transaction
binding owner + source hashes) and EXECUTED off-chain by the Python Cloud's hardened sandbox —
consensus itself never runs Python. Inside the sandbox your code reaches the platform via
``import animica`` (animica.ai.infer, animica.chain, animica.wallet.pay, animica.state,
animica.http.fetch, animica.call, animica.log, animica.secret), mediated by the declared
capabilities and paid in ANM.

Sibling SDK: ``animica.studio`` dispatches pickled callables to the AICF compute fleet; this
package deploys *source* to the managed HTTP platform. Same family, different substrate.
"""

from __future__ import annotations

from .app import App, ExtractedFunction, Extraction, Function, extract, strip_sdk_source
from .client import CloudClient
from .config import (
    CAPABILITIES,
    CloudConfig,
    NANM_PER_ANM,
    anm_to_nanm,
    format_anm,
    nanm_to_anm,
)
from .errors import (
    ApiError,
    AuthError,
    CloudError,
    ConfigError,
    ExtractionError,
    NetworkError,
    NotDeployedError,
    NotFoundError,
    RateLimitedError,
    ValidationFailed,
)

__all__ = [
    "App",
    "Function",
    "CloudClient",
    "CloudConfig",
    "Extraction",
    "ExtractedFunction",
    "extract",
    "strip_sdk_source",
    "CAPABILITIES",
    "NANM_PER_ANM",
    "anm_to_nanm",
    "nanm_to_anm",
    "format_anm",
    "CloudError",
    "ConfigError",
    "ExtractionError",
    "NotDeployedError",
    "NetworkError",
    "ApiError",
    "AuthError",
    "NotFoundError",
    "RateLimitedError",
    "ValidationFailed",
]
