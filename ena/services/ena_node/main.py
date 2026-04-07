"""
ENA Node - FastAPI server with payment gates and local operator workflows.

Provides:
- Payment-gated inference for network mode
- Dev-mode local inference and chat
- Local training job orchestration
- Checkpoint publishing and fetch endpoints
- Basic model download/export endpoints
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
import uuid
import zipfile
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field, validator

# Add parent directory to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

from ena.animica.address import (normalize_address, normalize_tx_hash,
                                 validate_address, validate_tx_hash)
from ena.animica.aicf_verify import (AICFVerificationError,
                                     calculate_aicf_split,
                                     verify_payment_and_aicf)
from ena.animica.animica_rpc import AnimicaRPCClient, CircuitOpenError
from ena.animica.verify import (TransactionVerificationError,
                                verify_payment_transaction)
from ena.inference import create_inference_engine
from ena.model_registry import ModelRegistry
from ena.services.ena_node.config import Config
from ena.services.ena_node.database import (CheckpointRecord, Database,
                                            TrainingJob)
from ena.services.ena_node.rate_limiter import RateLimiter
from ena.workers.train_worker import TrainingWorker

Config.ensure_dirs()


def _configure_logging() -> logging.Logger:
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if Config.LOG_FILE:
        handlers.append(logging.FileHandler(Config.LOG_FILE))
    logging.basicConfig(
        level=getattr(logging, Config.LOG_LEVEL),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=handlers,
    )
    return logging.getLogger(__name__)


logger = _configure_logging()

app = FastAPI(
    title="ENA - Animica LLM Inference Service",
    description="CPU-first LLM inference with blockchain payment and local operator workflows",
    version="0.1.0",
)

# Global state
rpc_client: Optional[AnimicaRPCClient] = None
database: Optional[Database] = None
rate_limiter: Optional[RateLimiter] = None
model_registry: Optional[ModelRegistry] = None
inference_engines: Dict[str, Any] = {}
training_tasks: Dict[str, asyncio.Task[Any]] = {}


@app.on_event("startup")
async def startup() -> None:
    """Initialize service on startup."""
    global rpc_client, database, rate_limiter, model_registry

    logger.info("Starting ENA node")
    logger.info("RPC URL: %s", Config.RPC_URL)
    logger.info("Service address: %s", Config.SERVICE_ADDRESS)
    logger.info("AICF address: %s", Config.AICF_ADDRESS)
    logger.info("AICF basis points: %s (%s%%)", Config.AICF_BP, Config.AICF_BP / 100)
    logger.info("AICF required: %s", Config.REQUIRE_AICF)
    logger.info("Dev mode: %s", Config.DEV_MODE)

    if Config.DEV_MODE:
        logger.warning("DEV MODE ENABLED - payment verification disabled")

    if not Config.REQUIRE_AICF and not Config.DEV_MODE:
        logger.warning("AICF not required; this should only be used for testing")

    rpc_client = AnimicaRPCClient(
        rpc_url=Config.RPC_URL,
        timeout=Config.RPC_TIMEOUT,
        max_retries=Config.RPC_MAX_RETRIES,
        retry_backoff=Config.RPC_RETRY_BACKOFF,
        circuit_breaker_threshold=Config.CIRCUIT_BREAKER_THRESHOLD,
        circuit_breaker_timeout=Config.CIRCUIT_BREAKER_TIMEOUT,
    )
    database = Database(Config.DB_PATH)
    rate_limiter = RateLimiter(
        requests_per_hour_address=Config.RATE_LIMIT_PER_ADDRESS,
        requests_per_hour_ip=Config.RATE_LIMIT_PER_IP,
    )
    model_registry = ModelRegistry(Config.MODELS_DIR)

    logger.info("ENA node started successfully")


@app.on_event("shutdown")
async def shutdown() -> None:
    """Clean up on shutdown."""
    global rpc_client

    logger.info("Shutting down ENA node")

    for task in list(training_tasks.values()):
        task.cancel()
    if training_tasks:
        await asyncio.gather(*training_tasks.values(), return_exceptions=True)
        training_tasks.clear()

    if rpc_client:
        rpc_client.close()

    logger.info("ENA node shut down")


class PaymentInfo(BaseModel):
    """Payment information for a request."""

    mode: str = Field(..., description="Payment mode: per_call_tx or credit")
    payer: str = Field(..., description="Payer address")
    tx_hash: Optional[str] = Field(None, description="Single payment transaction hash")
    tx_hash_service: Optional[str] = Field(None, description="Service payment tx hash")
    tx_hash_aicf: Optional[str] = Field(None, description="AICF payment tx hash")
    call_nonce: Optional[int] = Field(None, description="Call nonce (credit mode)")
    sig: Optional[str] = Field(None, description="Signature (credit mode)")

    @validator("mode")
    def validate_mode(cls, value: str) -> str:
        if value not in ["per_call_tx", "credit"]:
            raise ValueError("mode must be 'per_call_tx' or 'credit'")
        return value

    @validator("payer")
    def validate_payer(cls, value: str) -> str:
        if not validate_address(value):
            raise ValueError(f"Invalid address: {value}")
        return normalize_address(value)

    @validator("tx_hash")
    def validate_tx_hash_field(cls, value: Optional[str], values: Dict[str, Any]) -> Optional[str]:
        if values.get("mode") == "per_call_tx" and value:
            if not validate_tx_hash(value):
                raise ValueError(f"Invalid tx_hash: {value}")
            return normalize_tx_hash(value)
        return value

    @validator("tx_hash_service")
    def validate_tx_hash_service(cls, value: Optional[str], values: Dict[str, Any]) -> Optional[str]:
        if values.get("mode") == "per_call_tx" and value:
            if not validate_tx_hash(value):
                raise ValueError(f"Invalid tx_hash_service: {value}")
            return normalize_tx_hash(value)
        return value

    @validator("tx_hash_aicf")
    def validate_tx_hash_aicf(cls, value: Optional[str], values: Dict[str, Any]) -> Optional[str]:
        if values.get("mode") == "per_call_tx" and value:
            if not validate_tx_hash(value):
                raise ValueError(f"Invalid tx_hash_aicf: {value}")
            return normalize_tx_hash(value)
        return value


class InferRequest(BaseModel):
    """Paid inference request."""

    prompt: str = Field(..., description="Input prompt", max_length=2000)
    model: Optional[str] = Field(None, description="Model name or alias")
    max_tokens: int = Field(100, description="Maximum tokens to generate", ge=1, le=500)
    temperature: float = Field(0.7, description="Sampling temperature", ge=0.0, le=1.0)
    payment: PaymentInfo = Field(..., description="Payment information")


class LocalInferRequest(BaseModel):
    """Dev-mode local inference request."""

    prompt: str = Field(..., description="Input prompt", max_length=2000)
    model: Optional[str] = Field(None, description="Model name or alias")
    max_tokens: int = Field(100, description="Maximum tokens to generate", ge=1, le=500)
    temperature: float = Field(0.7, description="Sampling temperature", ge=0.0, le=1.0)


class ExportModelRequest(BaseModel):
    """Model export request."""

    model: str = Field(..., description="Model name or alias")
    format: str = Field("onnx", description="Export format")


class CheckpointPublishRequest(BaseModel):
    """Checkpoint publish request."""

    job_id: str = Field(..., description="Training job identifier")


class UsageInfo(BaseModel):
    """Token usage information."""

    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class ReceiptInfo(BaseModel):
    """Payment receipt."""

    id: str
    paid: bool
    mode: str
    tx_hash: Optional[str] = None
    tx_hash_service: Optional[str] = None
    tx_hash_aicf: Optional[str] = None
    amount: int
    service_paid: Optional[int] = None
    aicf_paid: Optional[int] = None
    aicf_required: Optional[int] = None
    aicf_explicit: Optional[bool] = None


class InferResponse(BaseModel):
    """Inference response."""

    ok: bool
    model: str
    usage: UsageInfo
    answer: str
    receipt: ReceiptInfo


class ModelInfo(BaseModel):
    """Model information."""

    name: str
    version: str
    description: str
    max_tokens: int


class ModelsResponse(BaseModel):
    """Models list response."""

    models: list[ModelInfo]
    aliases: Dict[str, str]
    default: str


class PricingInfo(BaseModel):
    """Pricing information."""

    fee_per_call: int
    fee_per_token: int
    currency: str = "ANM"
    base_units: int = 1_000_000_000
    aicf_address: str
    aicf_bp: int
    aicf_description: str = "AI Compute Fund - supports AI infrastructure"
    example_call_cost: int
    example_aicf_cost: int
    example_service_cost: int


def _require_database() -> Database:
    if database is None:
        raise HTTPException(status_code=503, detail="Database not initialized")
    return database


def _require_model_registry() -> ModelRegistry:
    if model_registry is None:
        raise HTTPException(status_code=503, detail="Model registry not initialized")
    return model_registry


def _require_rate_limiter() -> RateLimiter:
    if rate_limiter is None:
        raise HTTPException(status_code=503, detail="Rate limiter not initialized")
    return rate_limiter


def _require_rpc_client() -> AnimicaRPCClient:
    if rpc_client is None:
        raise HTTPException(status_code=503, detail="RPC client not initialized")
    return rpc_client


def get_client_ip(request: Request) -> str:
    """Get client IP from request."""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host
    return "unknown"


def calculate_cost(prompt_tokens: int, completion_tokens: int) -> int:
    """Calculate total cost for a request."""
    return Config.FEE_PER_CALL + (completion_tokens * Config.FEE_PER_TOKEN)


def verify_admin_token(authorization: Optional[str] = Header(None)) -> bool:
    """Verify admin token from Authorization header."""
    if not authorization:
        raise HTTPException(status_code=401, detail="Authorization required")

    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(status_code=401, detail="Invalid authorization format")

    if parts[1] != Config.ADMIN_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid token")
    return True


def _usage_payload(result: Dict[str, Any]) -> Dict[str, int]:
    usage = result["usage"]
    return {
        "prompt_tokens": int(usage["promptTokens"]),
        "completion_tokens": int(usage["completionTokens"]),
        "total_tokens": int(usage["totalTokens"]),
    }


def _execute_inference(
    prompt: str,
    *,
    model_name: Optional[str],
    max_tokens: int,
    temperature: float,
) -> tuple[str, Dict[str, Any]]:
    registry = _require_model_registry()
    resolved_model = model_name or Config.DEFAULT_MODEL
    model_info = registry.get_model(resolved_model)
    if not model_info:
        raise HTTPException(status_code=404, detail=f"Model not found: {resolved_model}")

    if model_info.name not in inference_engines:
        inference_engines[model_info.name] = create_inference_engine(
            model_info.path,
            model_info.name,
        )

    engine = inference_engines[model_info.name]
    result = engine.infer(
        prompt=prompt,
        max_tokens=max_tokens,
        temperature=temperature,
    )
    return model_info.name, result


def _job_to_dict(job: TrainingJob) -> Dict[str, Any]:
    return {
        "job_id": job.job_id,
        "payer": job.payer,
        "model": job.model,
        "plan": job.plan,
        "budget": job.budget,
        "spent": job.spent,
        "status": job.status,
        "progress": job.progress,
        "message": job.message,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
        "checkpoint_version": job.checkpoint_version,
        "output_dir": job.output_dir,
    }


def _checkpoint_to_dict(record: CheckpointRecord) -> Dict[str, Any]:
    return {
        "version": record.version,
        "job_id": record.job_id,
        "model": record.model,
        "epoch": record.epoch,
        "size_bytes": record.size_bytes,
        "published_at": record.published_at,
        "path": record.path,
        "metadata": record.metadata,
    }


def _checkpoint_version_for_job(job: TrainingJob, epoch: int) -> str:
    sanitized_model = job.model.replace("/", "_").replace(":", "_")
    return f"{sanitized_model}-{job.job_id[:8]}-epoch{epoch}"


def _publish_checkpoint_bundle(job_id: str) -> CheckpointRecord:
    db = _require_database()
    existing = db.get_checkpoint_for_job(job_id)
    if existing:
        return existing

    job = db.get_training_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Training job not found: {job_id}")
    if not job.output_dir:
        raise HTTPException(status_code=409, detail=f"Training job has no output directory: {job_id}")

    output_dir = Path(job.output_dir)
    if not output_dir.exists():
        raise HTTPException(status_code=409, detail=f"Training output missing: {output_dir}")

    checkpoint_json = output_dir / "checkpoint.json"
    metrics_json = output_dir / "metrics.json"
    model_dir = output_dir / "model"

    checkpoint_data: Dict[str, Any] = {}
    if checkpoint_json.exists():
        checkpoint_data = json.loads(checkpoint_json.read_text())
    epoch = int(checkpoint_data.get("epoch", 0))
    version = job.checkpoint_version or _checkpoint_version_for_job(job, epoch)

    bundle_path = Path(Config.CHECKPOINTS_DIR) / f"{version}.ckpt"
    metadata: Dict[str, Any] = {
        "job_id": job.job_id,
        "payer": job.payer,
        "plan": job.plan,
        "output_dir": str(output_dir),
    }
    if metrics_json.exists():
        metadata["metrics"] = json.loads(metrics_json.read_text())
    if checkpoint_data:
        metadata["checkpoint"] = checkpoint_data

    with zipfile.ZipFile(bundle_path, "w", zipfile.ZIP_DEFLATED) as archive:
        if checkpoint_json.exists():
            archive.write(checkpoint_json, arcname="checkpoint.json")
        if metrics_json.exists():
            archive.write(metrics_json, arcname="metrics.json")
        if model_dir.exists():
            for path in sorted(model_dir.rglob("*")):
                if path.is_file():
                    archive.write(path, arcname=str(Path("model") / path.relative_to(model_dir)))
        archive.writestr("job.json", json.dumps(_job_to_dict(job), indent=2))

    db.save_checkpoint(
        version=version,
        job_id=job.job_id,
        model=job.model,
        epoch=epoch,
        size_bytes=bundle_path.stat().st_size,
        path=str(bundle_path),
        metadata=metadata,
    )
    db.update_training_job(job_id, checkpoint_version=version)
    record = db.get_checkpoint(version)
    if record is None:
        raise HTTPException(status_code=500, detail="Checkpoint persistence failed")
    return record


async def _run_training_job(job_id: str) -> None:
    db = _require_database()
    job = db.get_training_job(job_id)
    if job is None:
        training_tasks.pop(job_id, None)
        return

    output_dir = Path(job.output_dir or (Path(Config.TRAINING_DIR) / job_id))
    db.update_training_job(
        job_id,
        status="running",
        progress=10,
        message="Preparing training worker",
        output_dir=str(output_dir),
    )

    job_spec = {
        "job_id": job.job_id,
        "job_type": str(job.plan.get("job_type") or "ena.train.sft"),
        "base_model": job.plan.get("base_model") or job.plan.get("model") or job.model,
        "dataset_hashes": job.plan.get("dataset_hashes") or [],
        "hyperparams": job.plan.get("hyperparams") or {},
        "max_gpu_hours": job.plan.get("max_gpu_hours"),
        "checkpoint_resume": job.plan.get("checkpoint_resume"),
    }
    worker = TrainingWorker(job_spec=job_spec, output_dir=output_dir, mock_mode=True)

    try:
        db.update_training_job(
            job_id,
            status="running",
            progress=35,
            message="Executing local training worker",
        )
        result = await asyncio.to_thread(worker.execute)
        if result.status != "success":
            db.update_training_job(
                job_id,
                status="failed",
                progress=100,
                message=result.error_message or "Training worker failed",
                output_dir=str(output_dir),
            )
            return

        spent = max(1, min(job.budget if job.budget > 0 else 1, max(1, job.budget // 2)))
        db.update_training_job(
            job_id,
            status="running",
            progress=85,
            spent=spent,
            message="Publishing checkpoint bundle",
            output_dir=str(output_dir),
        )
        checkpoint = await asyncio.to_thread(_publish_checkpoint_bundle, job_id)
        db.update_training_job(
            job_id,
            status="completed",
            progress=100,
            spent=spent,
            message="Training complete",
            checkpoint_version=checkpoint.version,
            output_dir=str(output_dir),
        )
    except asyncio.CancelledError:
        db.update_training_job(
            job_id,
            status="cancelled",
            message="Training cancelled",
            output_dir=str(output_dir),
        )
        raise
    except Exception as exc:  # noqa: BLE001
        logger.error("Training job %s failed: %s", job_id, exc, exc_info=True)
        db.update_training_job(
            job_id,
            status="failed",
            progress=100,
            message=str(exc),
            output_dir=str(output_dir),
        )
    finally:
        training_tasks.pop(job_id, None)


@app.get("/health")
@app.get("/healthz")
@app.get("/v1/health")
async def health() -> Dict[str, Any]:
    """Health check endpoint."""
    return {
        "status": "ok",
        "version": "0.1.0",
        "dev_mode": Config.DEV_MODE,
        "capabilities": {
            "pricing": True,
            "models": True,
            "paid_inference": True,
            "local_inference": True,
            "chat": Config.DEV_MODE,
            "training": True,
            "checkpoints": True,
        },
    }


@app.get("/v1/pricing")
async def get_pricing() -> PricingInfo:
    """Get pricing information including AICF contribution details."""
    example_total = Config.FEE_PER_CALL
    service_fee, aicf_fee = calculate_aicf_split(example_total, Config.AICF_BP)
    return PricingInfo(
        fee_per_call=Config.FEE_PER_CALL,
        fee_per_token=Config.FEE_PER_TOKEN,
        aicf_address=Config.AICF_ADDRESS,
        aicf_bp=Config.AICF_BP,
        example_call_cost=example_total,
        example_aicf_cost=aicf_fee,
        example_service_cost=service_fee,
    )


@app.get("/v1/models")
async def list_models() -> ModelsResponse:
    """List available models."""
    registry = _require_model_registry()
    models = registry.list_models()
    aliases = registry.list_aliases()
    default_model = registry.get_default()
    return ModelsResponse(
        models=[
            ModelInfo(
                name=model.name,
                version=model.version,
                description=model.description,
                max_tokens=model.max_tokens,
            )
            for model in models
        ],
        aliases=aliases,
        default=default_model.name if default_model else "",
    )


@app.get("/v1/models/pull/{model_name:path}")
async def pull_model(model_name: str) -> FileResponse:
    """Download the raw model artifact for a registered model."""
    registry = _require_model_registry()
    model_info = registry.get_model(model_name)
    if not model_info:
        raise HTTPException(status_code=404, detail=f"Model not found: {model_name}")
    model_path = Path(model_info.path)
    if not model_path.exists():
        raise HTTPException(status_code=404, detail=f"Model artifact missing: {model_path}")
    return FileResponse(model_path, media_type="application/octet-stream", filename=model_path.name)


@app.post("/v1/models/export")
async def export_model(request_data: ExportModelRequest) -> FileResponse:
    """Export a model by returning the current model artifact with a new extension."""
    registry = _require_model_registry()
    model_info = registry.get_model(request_data.model)
    if not model_info:
        raise HTTPException(status_code=404, detail=f"Model not found: {request_data.model}")
    model_path = Path(model_info.path)
    if not model_path.exists():
        raise HTTPException(status_code=404, detail=f"Model artifact missing: {model_path}")
    filename = f"{model_info.name.replace('/', '_')}.{request_data.format}"
    return FileResponse(model_path, media_type="application/octet-stream", filename=filename)


@app.post("/v1/infer")
async def infer(request_data: InferRequest, request: Request) -> InferResponse:
    """
    Run inference with payment verification.

    Requires valid payment unless the service is in dev mode.
    """
    db = _require_database()
    limiter = _require_rate_limiter()
    request_id = str(uuid.uuid4())
    client_ip = get_client_ip(request)

    logger.info(
        "Inference request: %s payer=%s mode=%s ip=%s",
        request_id,
        request_data.payment.payer,
        request_data.payment.mode,
        client_ip,
    )

    try:
        if not limiter.check(request_data.payment.payer, client_ip):
            raise HTTPException(status_code=429, detail="Rate limit exceeded")

        if len(request_data.prompt) > Config.MAX_PROMPT_LENGTH:
            raise HTTPException(
                status_code=400,
                detail=f"Prompt too long (max {Config.MAX_PROMPT_LENGTH})",
            )

        amount_paid = 0
        service_paid = 0
        aicf_paid = 0
        aicf_required = 0
        aicf_explicit = False
        tx_hash = None
        tx_hash_service = None
        tx_hash_aicf = None
        resolved_model = request_data.model or Config.DEFAULT_MODEL

        if not Config.DEV_MODE:
            if request_data.payment.mode == "per_call_tx":
                total_required = Config.MIN_FEE_PER_CALL
                service_required, aicf_required = calculate_aicf_split(
                    total_required,
                    Config.AICF_BP,
                )
                tx_hash = request_data.payment.tx_hash
                tx_hash_service = request_data.payment.tx_hash_service
                tx_hash_aicf = request_data.payment.tx_hash_aicf

                if tx_hash and db.is_transaction_used(tx_hash):
                    raise HTTPException(status_code=400, detail="Transaction already used (replay protection)")
                if tx_hash_service and db.is_transaction_used(tx_hash_service):
                    raise HTTPException(status_code=400, detail="Service transaction already used (replay protection)")
                if tx_hash_aicf and db.is_transaction_used(tx_hash_aicf):
                    raise HTTPException(status_code=400, detail="AICF transaction already used (replay protection)")

                try:
                    receipt = verify_payment_and_aicf(
                        rpc_client=_require_rpc_client(),
                        payer=request_data.payment.payer,
                        service_address=Config.SERVICE_ADDRESS,
                        aicf_address=Config.AICF_ADDRESS,
                        total_required=total_required,
                        aicf_bp=Config.AICF_BP,
                        tx_hash=tx_hash,
                        tx_hash_service=tx_hash_service,
                        tx_hash_aicf=tx_hash_aicf,
                        require_confirmed=False,
                    )
                except CircuitOpenError:
                    raise HTTPException(
                        status_code=503,
                        detail="RPC service unavailable - cannot verify payment",
                    ) from None
                except AICFVerificationError as exc:
                    raise HTTPException(
                        status_code=400,
                        detail=f"AICF contribution missing/insufficient: {exc}",
                    ) from exc
                except TransactionVerificationError as exc:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Payment verification failed: {exc}",
                    ) from exc

                amount_paid = int(receipt.get("totalPaid", 0))
                service_paid = int(receipt.get("servicePaid", 0))
                aicf_paid = int(receipt.get("aicfPaid", 0))
                aicf_explicit = bool(receipt.get("aicfExplicit", False))
                tx_hash = receipt.get("txHash")
                tx_hash_service = receipt.get("txHashService")
                tx_hash_aicf = receipt.get("txHashAicf")

                if tx_hash:
                    db.mark_transaction_used(
                        tx_hash=tx_hash,
                        payer=request_data.payment.payer,
                        amount=amount_paid,
                        request_id=request_id,
                    )
                if tx_hash_service:
                    db.mark_transaction_used(
                        tx_hash=tx_hash_service,
                        payer=request_data.payment.payer,
                        amount=service_paid or service_required,
                        request_id=request_id,
                    )
                if tx_hash_aicf:
                    db.mark_transaction_used(
                        tx_hash=tx_hash_aicf,
                        payer=request_data.payment.payer,
                        amount=aicf_paid or aicf_required,
                        request_id=request_id,
                    )
            elif request_data.payment.mode == "credit":
                estimated_cost = Config.FEE_PER_CALL + (request_data.max_tokens * Config.FEE_PER_TOKEN)
                if not db.deduct_credits(request_data.payment.payer, estimated_cost):
                    raise HTTPException(status_code=402, detail="Insufficient credits")
                amount_paid = estimated_cost
        else:
            logger.warning("DEV MODE: skipping payment verification for %s", request_id)

        model_name, result = _execute_inference(
            request_data.prompt,
            model_name=resolved_model,
            max_tokens=request_data.max_tokens,
            temperature=request_data.temperature,
        )
        actual_cost = calculate_cost(
            result["usage"]["promptTokens"],
            result["usage"]["completionTokens"],
        )

        if request_data.payment.mode == "credit" and not Config.DEV_MODE and actual_cost < amount_paid:
            refund = amount_paid - actual_cost
            db.add_credits(request_data.payment.payer, refund)
            amount_paid = actual_cost

        db.log_request(
            request_id=request_id,
            payer=request_data.payment.payer,
            model=model_name,
            mode=request_data.payment.mode,
            tx_hash=tx_hash,
            amount_paid=amount_paid,
            prompt_tokens=result["usage"]["promptTokens"],
            completion_tokens=result["usage"]["completionTokens"],
            total_tokens=result["usage"]["totalTokens"],
            success=True,
        )

        return InferResponse(
            ok=True,
            model=model_name,
            usage=UsageInfo(**_usage_payload(result)),
            answer=result["answer"],
            receipt=ReceiptInfo(
                id=request_id,
                paid=True,
                mode=request_data.payment.mode,
                tx_hash=tx_hash,
                tx_hash_service=tx_hash_service,
                tx_hash_aicf=tx_hash_aicf,
                amount=amount_paid,
                service_paid=service_paid if service_paid > 0 else None,
                aicf_paid=aicf_paid if aicf_paid > 0 else None,
                aicf_required=aicf_required if aicf_required > 0 else None,
                aicf_explicit=aicf_explicit if aicf_paid > 0 else None,
            ),
        )
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.error("Inference failed: %s", exc, exc_info=True)
        db.log_request(
            request_id=request_id,
            payer=request_data.payment.payer,
            model=request_data.model or Config.DEFAULT_MODEL,
            mode=request_data.payment.mode,
            tx_hash=request_data.payment.tx_hash,
            amount_paid=0,
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
            success=False,
            error=str(exc),
        )
        raise HTTPException(status_code=500, detail=f"Internal error: {exc}") from exc


@app.post("/v1/inference")
async def local_inference(request_data: LocalInferRequest) -> Dict[str, Any]:
    """Run a local operator/dev inference request without payment."""
    if not Config.DEV_MODE:
        raise HTTPException(status_code=403, detail="Local inference endpoint requires ENA_DEV_MODE=1")

    model_name, result = _execute_inference(
        request_data.prompt,
        model_name=request_data.model,
        max_tokens=request_data.max_tokens,
        temperature=request_data.temperature,
    )
    return {
        "ok": True,
        "model": model_name,
        "text": result["answer"],
        "answer": result["answer"],
        "usage": _usage_payload(result),
    }


@app.post("/chat")
async def chat(payload: Dict[str, Any]) -> StreamingResponse:
    """Studio-compatible dev chat endpoint."""
    if not Config.DEV_MODE:
        raise HTTPException(status_code=403, detail="Chat endpoint requires ENA_DEV_MODE=1")

    messages = payload.get("messages") or []
    prompt = ""
    if messages and isinstance(messages[-1], dict):
        prompt = str(messages[-1].get("content", ""))
    model_name = payload.get("model") or Config.DEFAULT_MODEL
    resolved_model, result = _execute_inference(
        prompt,
        model_name=model_name,
        max_tokens=payload.get("max_tokens", 100),
        temperature=payload.get("temperature", 0.7),
    )
    answer = result["answer"]

    def _event_stream() -> Any:
        for token in answer.split():
            yield f"data: {json.dumps({'type': 'token', 'text': token + ' '})}\n\n"
        yield f"data: {json.dumps({'type': 'done', 'model': resolved_model})}\n\n"

    return StreamingResponse(_event_stream(), media_type="text/event-stream")


@app.post("/training/submit")
@app.post("/v1/training/submit")
async def submit_training_job(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Submit a local training job."""
    db = _require_database()

    plan = payload.get("plan")
    if not isinstance(plan, dict):
        if payload:
            plan = {"bundle_ref": payload}
        else:
            raise HTTPException(status_code=400, detail="Training plan is required")

    budget = int(payload.get("budget") or 0)
    payer = str(payload.get("payer") or payload.get("owner") or payload.get("address") or "local-dev")
    model_name = str(plan.get("model") or plan.get("base_model") or Config.DEFAULT_MODEL)

    job_id = str(payload.get("job_id") or uuid.uuid4())
    while db.get_training_job(job_id) is not None:
        job_id = str(uuid.uuid4())

    output_dir = str(Path(Config.TRAINING_DIR) / job_id)
    db.create_training_job(
        job_id=job_id,
        payer=payer,
        model=model_name,
        plan=plan,
        budget=budget,
        status="pending",
        progress=0,
        message="Job accepted",
        output_dir=output_dir,
    )
    training_tasks[job_id] = asyncio.create_task(_run_training_job(job_id))

    return {
        "ok": True,
        "job_id": job_id,
        "status": "pending",
        "budget": budget,
        "payer": payer,
        "model": model_name,
        "output_dir": output_dir,
    }


@app.get("/training/list")
@app.get("/v1/training/list")
async def list_training_jobs(status: Optional[str] = None, limit: int = 20) -> Dict[str, Any]:
    """List training jobs."""
    db = _require_database()
    jobs = db.list_training_jobs(status=status, limit=limit)
    return {"jobs": [_job_to_dict(job) for job in jobs], "total": len(jobs)}


@app.get("/training/status/{job_id}")
@app.get("/v1/training/status/{job_id}")
async def training_status(job_id: str) -> Dict[str, Any]:
    """Get a single training job status."""
    db = _require_database()
    job = db.get_training_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Training job not found: {job_id}")
    return _job_to_dict(job)


@app.get("/checkpoints/list")
@app.get("/v1/checkpoints/list")
async def checkpoints_list(model: Optional[str] = None, limit: int = 20) -> Dict[str, Any]:
    """List published checkpoints."""
    db = _require_database()
    checkpoints = db.list_checkpoints(model=model, limit=limit)
    return {
        "checkpoints": [_checkpoint_to_dict(record) for record in checkpoints],
        "total": len(checkpoints),
    }


@app.post("/checkpoints/publish")
@app.post("/v1/checkpoints/publish")
async def checkpoints_publish(request_data: CheckpointPublishRequest) -> Dict[str, Any]:
    """Publish a checkpoint bundle for a completed local training job."""
    db = _require_database()
    job = db.get_training_job(request_data.job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Training job not found: {request_data.job_id}")
    if job.status not in {"running", "completed"}:
        raise HTTPException(status_code=409, detail=f"Training job not ready for checkpoint publish: {job.status}")

    record = await asyncio.to_thread(_publish_checkpoint_bundle, request_data.job_id)
    return {
        "ok": True,
        "version": record.version,
        "job_id": record.job_id,
        "path": record.path,
        "size_bytes": record.size_bytes,
    }


@app.get("/checkpoints/fetch/{version}")
@app.get("/v1/checkpoints/fetch/{version}")
async def checkpoints_fetch(version: str) -> FileResponse:
    """Fetch a published checkpoint bundle."""
    db = _require_database()
    record = db.get_checkpoint(version)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Checkpoint not found: {version}")
    checkpoint_path = Path(record.path)
    if not checkpoint_path.exists():
        raise HTTPException(status_code=404, detail=f"Checkpoint artifact missing: {checkpoint_path}")
    return FileResponse(
        checkpoint_path,
        media_type="application/octet-stream",
        filename=checkpoint_path.name,
    )


@app.post("/admin/set_default_model")
async def set_default_model(
    model_name: str,
    _: bool = Depends(verify_admin_token),
) -> Dict[str, Any]:
    """Set the default model (admin only)."""
    registry = _require_model_registry()
    try:
        registry.set_default(model_name)
        return {"ok": True, "default": model_name}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/admin/set_alias")
async def set_alias(
    alias: str,
    target: str,
    _: bool = Depends(verify_admin_token),
) -> Dict[str, Any]:
    """Set a model alias (admin only)."""
    registry = _require_model_registry()
    try:
        registry.set_alias(alias, target)
        return {"ok": True, "alias": alias, "target": target}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/admin/reload_models")
async def reload_models(_: bool = Depends(verify_admin_token)) -> Dict[str, Any]:
    """Reload models from directory (admin only)."""
    registry = _require_model_registry()
    registry.reload()
    inference_engines.clear()
    return {"ok": True}


def main() -> None:
    """CLI entry point."""
    import uvicorn

    uvicorn.run(
        app,
        host=Config.HOST,
        port=Config.PORT,
        log_level=Config.LOG_LEVEL.lower(),
    )


if __name__ == "__main__":
    main()
