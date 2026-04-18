from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .adapters.animica import AnimicaRpcAdapter
from .adapters.evm import Web3EvmAdapter
from .auth import AdminPrincipal, hash_password, sign_token, verify_password, verify_token
from .config import BridgeBanmConfig, load_config
from .db import build_engine, build_sessionmaker
from .engine import BridgeEngine
from .enums import BridgeDirection
from .models import Base
from .repository import BridgeRepository
from .schemas import (
    AdminLoginRequest,
    AdminLoginResponse,
    ClaimCodeConfirmRequest,
    CreateOrderRequest,
    CreateOrderResponse,
    DepositAttachRequest,
    OrderStatusResponse,
    PauseRequest,
    SignatureVerifyRequest,
    SolvencyResponse,
)
from .workers import BridgeWorker

log = logging.getLogger(__name__)


class SimpleRateLimiter:
    def __init__(self, *, max_requests: int, window_seconds: int):
        self.max_requests = max_requests
        self.window = timedelta(seconds=window_seconds)
        self._buckets: dict[str, list[datetime]] = {}

    def allow(self, key: str) -> bool:
        now = datetime.now(timezone.utc)
        cutoff = now - self.window
        timestamps = [ts for ts in self._buckets.get(key, []) if ts >= cutoff]
        if len(timestamps) >= self.max_requests:
            self._buckets[key] = timestamps
            return False
        timestamps.append(now)
        self._buckets[key] = timestamps
        return True


@dataclass
class ServiceContext:
    config: BridgeBanmConfig
    repository: BridgeRepository
    engine: BridgeEngine
    worker: BridgeWorker | None


def _build_context(config: BridgeBanmConfig) -> ServiceContext:
    session_factory = build_sessionmaker(config.database_url)
    engine = build_engine(config.database_url)
    Base.metadata.create_all(bind=engine)

    repository = BridgeRepository(session_factory)
    animica = AnimicaRpcAdapter(config.animica_rpc_url)
    evm = Web3EvmAdapter(
        rpc_url=config.evm_rpc_url,
        chain_id=config.evm_chain_id,
        token_address=config.evm_banm_token_address,
        controller_address=config.evm_bridge_controller_address,
        vault_address=config.evm_bridge_vault_address,
        router_address=config.evm_bridge_deposit_router_address,
        operator_private_key=config.evm_operator_private_key,
    )
    bridge_engine = BridgeEngine(config=config, repository=repository, animica=animica, evm=evm)
    worker = BridgeWorker(bridge_engine, config.worker_poll_interval_seconds) if config.worker_enabled else None
    return ServiceContext(config=config, repository=repository, engine=bridge_engine, worker=worker)


def create_app(config: BridgeBanmConfig | None = None) -> FastAPI:
    cfg = config or load_config()
    context = _build_context(cfg)
    app = FastAPI(title="BANM Custodial Bridge API", version="0.1.0")
    app.state.context = context
    app.state.worker_task = None
    app.state.public_rate_limiter = SimpleRateLimiter(max_requests=20, window_seconds=60)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.on_event("startup")
    async def _on_startup() -> None:
        ctx: ServiceContext = app.state.context
        if ctx.worker is not None:
            app.state.worker_task = asyncio.create_task(ctx.worker.run_forever())
            log.info("BANM worker started")

    @app.on_event("shutdown")
    async def _on_shutdown() -> None:
        task = app.state.worker_task
        ctx: ServiceContext = app.state.context
        if ctx.worker is not None:
            ctx.worker.stop()
        if task is not None:
            task.cancel()
            try:
                await task
            except Exception:
                pass

    def get_ctx() -> ServiceContext:
        return app.state.context

    def _admin_principal(
        authorization: Annotated[str | None, Header(alias="Authorization")] = None,
        ctx: ServiceContext = Depends(get_ctx),
    ) -> AdminPrincipal:
        if not authorization or not authorization.lower().startswith("bearer "):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="missing bearer token")
        token = authorization.split(" ", 1)[1].strip()
        try:
            payload = verify_token(token, secret=ctx.config.bridge_admin_token_secret)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
        username = str(payload.get("sub") or "")
        role = str(payload.get("role") or "viewer")
        if not username:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid token subject")
        return AdminPrincipal(username=username, role=role)

    @app.exception_handler(ValueError)
    async def _value_error_handler(_: Request, exc: ValueError):
        return JSONResponse(status_code=status.HTTP_400_BAD_REQUEST, content={"detail": str(exc)})

    @app.get("/healthz")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz")
    def ready(ctx: ServiceContext = Depends(get_ctx)) -> dict[str, str]:
        flags = ctx.engine.get_pause_flags()
        return {"status": "ready", **{k: str(v).lower() for k, v in flags.items()}}

    @app.post("/api/v1/orders", response_model=CreateOrderResponse)
    def create_order(
        raw_request: Request,
        request: CreateOrderRequest,
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
        ctx: ServiceContext = Depends(get_ctx),
    ) -> CreateOrderResponse:
        rate_key = raw_request.client.host if raw_request.client else "unknown"
        if not app.state.public_rate_limiter.allow(rate_key):
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="rate limit exceeded for order creation",
            )
        return ctx.engine.create_order(request, idempotency_key=idempotency_key)

    @app.post("/api/v1/orders/{order_id}/signature/verify")
    def verify_signature(
        order_id: str,
        request: SignatureVerifyRequest,
        ctx: ServiceContext = Depends(get_ctx),
    ):
        order = ctx.engine.verify_signature(
            order_id=order_id,
            signature=request.signature,
            signature_type=request.signature_type,
        )
        return {"order": order}

    @app.post("/api/v1/orders/{order_id}/claim-code/confirm")
    def confirm_claim_code(
        order_id: str,
        request: ClaimCodeConfirmRequest,
        ctx: ServiceContext = Depends(get_ctx),
    ):
        order = ctx.engine.confirm_claim_code(order_id=order_id, claim_code=request.claim_code)
        return {"order": order}

    @app.post("/api/v1/orders/{order_id}/deposits/animica")
    def attach_animica_deposit(
        order_id: str,
        request: DepositAttachRequest,
        ctx: ServiceContext = Depends(get_ctx),
    ):
        order = ctx.engine.attach_animica_deposit_tx(order_id=order_id, tx_hash=request.tx_hash)
        return {"order": order}

    @app.post("/api/v1/orders/{order_id}/deposits/evm")
    def attach_evm_deposit(
        order_id: str,
        request: DepositAttachRequest,
        ctx: ServiceContext = Depends(get_ctx),
    ):
        order = ctx.engine.attach_evm_deposit_tx(order_id=order_id, tx_hash=request.tx_hash)
        return {"order": order}

    @app.get("/api/v1/orders/{order_id}", response_model=OrderStatusResponse)
    def order_status(order_id: str, ctx: ServiceContext = Depends(get_ctx)) -> OrderStatusResponse:
        return ctx.engine.get_order_status(order_id)

    @app.get("/api/v1/solvency/public", response_model=SolvencyResponse)
    def public_solvency(ctx: ServiceContext = Depends(get_ctx)) -> SolvencyResponse:
        return ctx.engine.compute_solvency()

    @app.post("/api/v1/admin/login", response_model=AdminLoginResponse)
    def admin_login(
        request: AdminLoginRequest,
        ctx: ServiceContext = Depends(get_ctx),
    ) -> AdminLoginResponse:
        user = ctx.repository.find_admin_user(request.username)
        if user is None or not user.is_active:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid credentials")
        if not verify_password(request.password, salt=user.password_salt, password_hash=user.password_hash):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid credentials")
        token = sign_token(
            {"sub": user.username, "role": user.role},
            secret=ctx.config.bridge_admin_token_secret,
            expires_in_seconds=8 * 3600,
        )
        ctx.repository.touch_admin_login(user.username)
        return AdminLoginResponse(access_token=token, role=user.role)

    @app.get("/api/v1/admin/orders")
    def admin_orders(
        principal: AdminPrincipal = Depends(_admin_principal),
        status_filter: str | None = Query(None, alias="status"),
        direction_filter: str | None = Query(None, alias="direction"),
        limit: int = Query(200, ge=1, le=1000),
        ctx: ServiceContext = Depends(get_ctx),
    ):
        status_value = None
        direction_value = None
        if status_filter:
            from .enums import BridgeStatus

            status_value = BridgeStatus(status_filter)
        if direction_filter:
            direction_value = BridgeDirection(direction_filter)
        orders = ctx.repository.list_orders(status=status_value, direction=direction_value, limit=limit)
        return {
            "actor": principal.username,
            "items": [ctx.engine.get_order_status(o.order_id).order for o in orders],
        }

    @app.get("/api/v1/admin/orders/{order_id}")
    def admin_order_detail(
        order_id: str,
        _: AdminPrincipal = Depends(_admin_principal),
        ctx: ServiceContext = Depends(get_ctx),
    ):
        return ctx.engine.get_order_status(order_id)

    @app.post("/api/v1/admin/orders/{order_id}/retry")
    def admin_retry_order(
        order_id: str,
        principal: AdminPrincipal = Depends(_admin_principal),
        ctx: ServiceContext = Depends(get_ctx),
    ):
        order = ctx.repository.get_order_or_raise(order_id)
        ctx.engine.poll_order_progress(order)
        return {"order": ctx.engine.get_order_status(order_id).order, "actor": principal.username}

    @app.post("/api/v1/admin/orders/{order_id}/manual-review")
    def admin_manual_review(
        order_id: str,
        principal: AdminPrincipal = Depends(_admin_principal),
        reason: str = Query(..., min_length=3),
        ctx: ServiceContext = Depends(get_ctx),
    ):
        order = ctx.repository.mark_manual_review(order_id, reason, actor=principal.username)
        return {"order": order.order_id, "status": order.status, "reason": reason}

    @app.post("/api/v1/admin/pause/{flag_name}")
    def admin_pause(
        flag_name: str,
        request: PauseRequest,
        principal: AdminPrincipal = Depends(_admin_principal),
        ctx: ServiceContext = Depends(get_ctx),
    ):
        if principal.role not in {"admin", "operator"}:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="insufficient role")
        ctx.engine.set_pause_flag(flag_name, request.paused, actor=principal.username)
        return {"flag": flag_name, "paused": request.paused}

    @app.get("/api/v1/admin/solvency")
    def admin_solvency(
        _: AdminPrincipal = Depends(_admin_principal),
        ctx: ServiceContext = Depends(get_ctx),
    ):
        public = ctx.engine.compute_solvency()
        return {
            "public": public,
            "pause_flags": ctx.engine.get_pause_flags(),
        }

    return app


def seed_admin_user(
    *,
    app: FastAPI,
    username: str,
    password: str,
    role: str = "admin",
) -> None:
    ctx: ServiceContext = app.state.context
    salt, pwd_hash = hash_password(password)
    ctx.repository.get_or_create_admin_user(
        username=username,
        role=role,
        password_salt=salt,
        password_hash=pwd_hash,
    )
