from __future__ import annotations

"""
Configuration loader for Animica Studio Services.

- Reads environment variables (optionally from `.env`) via pydantic-settings.
- Provides strongly typed sub-configs for CORS, security, rate limits, and storage.
- Exposes a cached `get_settings()` accessor.

Environment variables (high-level):
    RPC_URL                       (str, required)       — Node JSON-RPC endpoint
    CHAIN_ID                      (int, default 1337)   — Chain id to enforce
    LOG_LEVEL                     (str, default "INFO") — Logging level

CORS:
    CORS_ALLOW_ORIGINS            (csv|json list)       — e.g. "http://localhost:5173,https://app.example"
    CORS_ALLOW_HEADERS            (csv|json list)
    CORS_ALLOW_METHODS            (csv|json list)
    CORS_ALLOW_CREDENTIALS        (bool, default False)

Security:
    API_KEYS                      (csv|json list)       — Optional API keys (bearer or query)
    FAUCET_KEY                    (hex, optional)       — Optional hot key for faucet (dev/test only)
    ENABLE_FAUCET                 (bool, default False) — Gate faucet routes
    ENABLE_VERIFY                 (bool, default True)  — Gate verify routes
    HOST_ALLOWLIST                (csv|json list)       — Optional host/origin allowlist for extra checks

Rate limits:
    RATE_GLOBAL_RPS               (float, default 25)
    RATE_GLOBAL_BURST             (int, default 50)
    RATE_LIMITS                   (json mapping)        — {"POST:/deploy":{"rps":2,"burst":5}, ...}

Storage:
    STORAGE_DIR                   (str, default "./.storage")

Notes
-----
- Lists accept comma-separated strings or JSON arrays.
- RATE_LIMITS must be JSON if provided.
"""

from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional

from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# ----------------------------- Helpers & Models ------------------------------ #


def _parse_list(val: Optional[str | List[str]], *, default: List[str]) -> List[str]:
    if val is None:
        return list(default)
    if isinstance(val, list):
        return val
    s = val.strip()
    if not s:
        return []
    # Try JSON first
    if s.startswith("[") and s.endswith("]"):
        try:
            import json

            parsed = json.loads(s)
            return [str(x) for x in parsed]
        except Exception:
            pass
    # Fallback: CSV
    return [x.strip() for x in s.split(",") if x.strip()]


class RateLimitSpec(BaseModel):
    """Token-bucket-ish spec (lightweight)."""

    rps: float = Field(ge=0, description="Allowed requests per second (average).")
    burst: int = Field(ge=0, description="Max burst size before throttling.")

    @classmethod
    def from_env_pair(
        cls,
        rps: float | str | None,
        burst: int | str | None,
        *,
        defaults: "RateLimitSpec",
    ) -> "RateLimitSpec":
        try:
            r = float(rps) if rps is not None else defaults.rps
        except Exception:
            r = defaults.rps
        try:
            b = int(burst) if burst is not None else defaults.burst
        except Exception:
            b = defaults.burst
        return cls(rps=r, burst=b)


class CorsConfig(BaseModel):
    allow_origins: List[str] = Field(
        default_factory=lambda: ["http://localhost:5173", "http://127.0.0.1:5173"]
    )
    allow_headers: List[str] = Field(
        default_factory=lambda: [
            "Authorization",
            "Content-Type",
            "X-Requested-With",
            "X-API-Key",
        ]
    )
    allow_methods: List[str] = Field(default_factory=lambda: ["GET", "POST", "OPTIONS"])
    allow_credentials: bool = False

    @field_validator("allow_origins", "allow_headers", "allow_methods", mode="before")
    @classmethod
    def _coerce_list(cls, v):
        return _parse_list(v, default=[])


class SecurityConfig(BaseModel):
    api_keys: List[str] = Field(
        default_factory=list, description="Accepted API keys for protected endpoints."
    )
    faucet_key: Optional[str] = Field(
        default=None, description="Optional hot key for faucet (dev/test only)."
    )
    enable_faucet: bool = False
    enable_verify: bool = True
    host_allowlist: List[str] = Field(default_factory=list)

    @field_validator("api_keys", "host_allowlist", mode="before")
    @classmethod
    def _coerce_list(cls, v):
        return _parse_list(v, default=[])


class RateLimitConfig(BaseModel):
    global_limit: RateLimitSpec = Field(
        default_factory=lambda: RateLimitSpec(rps=25.0, burst=50)
    )
    route_overrides: Dict[str, RateLimitSpec] = Field(default_factory=dict)

    @field_validator("route_overrides", mode="before")
    @classmethod
    def _parse_overrides(cls, v):
        if v is None:
            return {}
        if isinstance(v, dict):
            # Could be already parsed from .env backed by pydantic
            return {
                str(k): (
                    RateLimitSpec(**vv) if not isinstance(vv, RateLimitSpec) else vv
                )
                for k, vv in v.items()
            }
        if isinstance(v, str):
            s = v.strip()
            if not s:
                return {}
            import json

            try:
                data = json.loads(s)
                return {str(k): RateLimitSpec(**vv) for k, vv in data.items()}
            except Exception as e:
                raise ValueError(
                    "RATE_LIMITS must be valid JSON mapping of route->spec"
                ) from e
        raise TypeError("Invalid type for rate limit overrides")


class StorageConfig(BaseModel):
    storage_dir: Path = Path("./.storage")

    @field_validator("storage_dir", mode="before")
    @classmethod
    def _coerce_path(cls, v):
        return Path(v) if v is not None else Path("./.storage")


# --------------------------------- Settings ---------------------------------- #


class Settings(BaseSettings):
    # Core
    rpc_url: str = Field("http://127.0.0.1:8545", description="Node JSON-RPC endpoint")
    chain_id: int = Field(1337, description="Network chain id to enforce")
    log_level: str = Field(
        "INFO", description="Logging level (DEBUG, INFO, WARNING, ERROR)"
    )

    # Sub-configs
    cors: CorsConfig = Field(default_factory=CorsConfig)
    security: SecurityConfig = Field(default_factory=SecurityConfig)
    rates: RateLimitConfig = Field(default_factory=RateLimitConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)

    # pydantic-settings
    model_config = SettingsConfigDict(
        env_prefix="", env_file=".env", case_sensitive=False, extra="ignore"
    )

    # --- Env bridges for convenience (.env keys -> nested models) ------------
    # CORS
    CORS_ALLOW_ORIGINS: Optional[str | List[str]] = Field(
        default=None, alias="CORS_ALLOW_ORIGINS"
    )
    CORS_ALLOW_HEADERS: Optional[str | List[str]] = Field(
        default=None, alias="CORS_ALLOW_HEADERS"
    )
    CORS_ALLOW_METHODS: Optional[str | List[str]] = Field(
        default=None, alias="CORS_ALLOW_METHODS"
    )
    CORS_ALLOW_CREDENTIALS: Optional[bool] = Field(
        default=None, alias="CORS_ALLOW_CREDENTIALS"
    )

    # Security
    API_KEYS: Optional[str | List[str]] = Field(default=None, alias="API_KEYS")
    FAUCET_KEY: Optional[str] = Field(default=None, alias="FAUCET_KEY")
    ENABLE_FAUCET: Optional[bool] = Field(default=None, alias="ENABLE_FAUCET")
    ENABLE_VERIFY: Optional[bool] = Field(default=None, alias="ENABLE_VERIFY")
    HOST_ALLOWLIST: Optional[str | List[str]] = Field(
        default=None, alias="HOST_ALLOWLIST"
    )

    # Rate limits
    RATE_GLOBAL_RPS: Optional[float] = Field(default=None, alias="RATE_GLOBAL_RPS")
    RATE_GLOBAL_BURST: Optional[int] = Field(default=None, alias="RATE_GLOBAL_BURST")
    RATE_LIMITS: Optional[dict | str] = Field(default=None, alias="RATE_LIMITS")

    # Storage
    STORAGE_DIR: Optional[str] = Field(default=None, alias="STORAGE_DIR")

    @field_validator("cors", mode="after")
    def _apply_cors_env(cls, v: CorsConfig, info):
        data = info.data  # full model input so far
        ao = data.get("CORS_ALLOW_ORIGINS")
        ah = data.get("CORS_ALLOW_HEADERS")
        am = data.get("CORS_ALLOW_METHODS")
        ac = data.get("CORS_ALLOW_CREDENTIALS")
        if ao is not None:
            v.allow_origins = _parse_list(ao, default=v.allow_origins)
        if ah is not None:
            v.allow_headers = _parse_list(ah, default=v.allow_headers)
        if am is not None:
            v.allow_methods = _parse_list(am, default=v.allow_methods)
        if ac is not None:
            v.allow_credentials = bool(ac)
        return v

    @field_validator("security", mode="after")
    def _apply_security_env(cls, v: SecurityConfig, info):
        data = info.data
        keys = data.get("API_KEYS")
        if keys is not None:
            v.api_keys = _parse_list(keys, default=v.api_keys)
        faucet_key = data.get("FAUCET_KEY")
        if faucet_key is not None:
            v.faucet_key = str(faucet_key) or None
        ef = data.get("ENABLE_FAUCET")
        if ef is not None:
            v.enable_faucet = bool(ef)
        ev = data.get("ENABLE_VERIFY")
        if ev is not None:
            v.enable_verify = bool(ev)
        ha = data.get("HOST_ALLOWLIST")
        if ha is not None:
            v.host_allowlist = _parse_list(ha, default=v.host_allowlist)
        return v

    @field_validator("rates", mode="after")
    def _apply_rate_env(cls, v: RateLimitConfig, info):
        data = info.data
        gl_rps = data.get("RATE_GLOBAL_RPS")
        gl_burst = data.get("RATE_GLOBAL_BURST")
        if gl_rps is not None or gl_burst is not None:
            v.global_limit = RateLimitSpec.from_env_pair(
                gl_rps, gl_burst, defaults=v.global_limit
            )
        overrides = data.get("RATE_LIMITS")
        if overrides is not None:
            v.route_overrides = RateLimitConfig.model_fields["route_overrides"].annotation.__get_validators__  # type: ignore[attr-defined]
            # Above trick isn't reliable across versions; just reuse parser:
            v.route_overrides = RateLimitConfig._parse_overrides(overrides)  # type: ignore
        return v

    @field_validator("storage", mode="after")
    def _apply_storage_env(cls, v: StorageConfig, info):
        sd = info.data.get("STORAGE_DIR")
        if sd:
            v.storage_dir = Path(sd)
        return v


# ------------------------------- Accessor API -------------------------------- #


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached settings instance."""
    s = Settings()  # pydantic-settings will read .env automatically
    # Ensure storage directory exists
    try:
        s.storage.storage_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        # Non-fatal: path may be read-only in some environments
        pass
    return s


__all__ = [
    "Settings",
    "Config",
    "CorsConfig",
    "SecurityConfig",
    "RateLimitSpec",
    "RateLimitConfig",
    "StorageConfig",
    "load_config",
    "get_settings",
]


class Config(Settings):
    """Compatibility wrapper around :class:`Settings`.

    The legacy codebase expects a ``Config`` object with a handful of
    uppercase attributes (e.g., ``CHAIN_ID``) and helper accessors for CORS
    and rate-limit configs.  We expose those as properties while retaining the
    strongly typed ``Settings`` fields.
    """

    # Legacy attribute shims -------------------------------------------------
    @property
    def RPC_URL(self) -> str:
        return self.rpc_url

    @property
    def CHAIN_ID(self) -> int:
        return self.chain_id

    @property
    def ENV(self) -> Optional[str]:
        # Optional marker used in health/version endpoints
        import os

        return os.getenv("STUDIO_SERVICES_ENV")

    # Helper builders --------------------------------------------------------
    def to_cors_config(self):
        """Convert to the security.cors CORSConfig model."""
        from .security.cors import CORSConfig

        return CORSConfig(
            allow_origins=list(self.cors.allow_origins),
            allow_origin_regex=None,
            allow_methods=list(self.cors.allow_methods),
            allow_headers=list(self.cors.allow_headers),
            expose_headers=[],
            allow_credentials=self.cors.allow_credentials,
            max_age=600,
            debug=False,
        )

    def to_rate_config(self):
        """Convert to the security.rate_limit RateConfig model."""
        from .security.rate_limit import RateConfig, RateRule

        global_limit = self.rates.global_limit
        route_rules = {
            path: RateRule(
                name=f"route:{path}",
                refill_per_sec=spec.rps,
                capacity=float(spec.burst),
            )
            for path, spec in self.rates.route_overrides.items()
        }

        return RateConfig(
            global_rule=RateRule(
                "global",
                refill_per_sec=global_limit.rps,
                capacity=float(global_limit.burst),
            ),
            ip_rule=RateRule(
                "ip",
                refill_per_sec=global_limit.rps,
                capacity=float(global_limit.burst),
            ),
            key_rule=RateRule(
                "key",
                refill_per_sec=global_limit.rps,
                capacity=float(global_limit.burst),
            ),
            route_rules=route_rules,
        )


@lru_cache(maxsize=1)
def load_config() -> Config:
    """Legacy entrypoint expected by app/CLI modules."""

    return Config()  # type: ignore[call-arg]
