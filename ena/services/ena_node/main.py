"""
ENA Node - FastAPI server with payment gates.

Provides LLM inference with on-chain payment verification including
mandatory AICF (AI Compute Fund) contributions.
"""

import logging
import os
import sys
import time
import uuid
from typing import Optional, Dict, Any

from fastapi import FastAPI, HTTPException, Request, Depends, Header
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, validator

# Add parent directory to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

from ena.animica.animica_rpc import AnimicaRPCClient, CircuitOpenError
from ena.animica.address import validate_address, validate_tx_hash, normalize_address, normalize_tx_hash
from ena.animica.verify import verify_payment_transaction, TransactionVerificationError
from ena.animica.aicf_verify import (
    verify_payment_and_aicf,
    calculate_aicf_split,
    AICFVerificationError,
)
from ena.model_registry import ModelRegistry
from ena.inference import create_inference_engine
from ena.services.ena_node.config import Config
from ena.services.ena_node.database import Database
from ena.services.ena_node.rate_limiter import RateLimiter

# Configure logging
logging.basicConfig(
    level=getattr(logging, Config.LOG_LEVEL),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(Config.LOG_FILE) if Config.LOG_FILE else logging.NullHandler(),
    ],
)

logger = logging.getLogger(__name__)

# Initialize components
Config.ensure_dirs()

app = FastAPI(
    title="ENA - Animica LLM Inference Service",
    description="CPU-first LLM inference with blockchain payment",
    version="0.1.0",
)

# Global state
rpc_client: Optional[AnimicaRPCClient] = None
database: Optional[Database] = None
rate_limiter: Optional[RateLimiter] = None
model_registry: Optional[ModelRegistry] = None
inference_engines: Dict[str, Any] = {}


@app.on_event("startup")
async def startup():
    """Initialize service on startup."""
    global rpc_client, database, rate_limiter, model_registry
    
    logger.info("Starting ENA node...")
    logger.info(f"RPC URL: {Config.RPC_URL}")
    logger.info(f"Service address: {Config.SERVICE_ADDRESS}")
    logger.info(f"AICF address: {Config.AICF_ADDRESS}")
    logger.info(f"AICF basis points: {Config.AICF_BP} ({Config.AICF_BP / 100}%)")
    logger.info(f"AICF required: {Config.REQUIRE_AICF}")
    logger.info(f"Dev mode: {Config.DEV_MODE}")
    
    if Config.DEV_MODE:
        logger.warning("=" * 60)
        logger.warning("DEV MODE ENABLED - PAYMENT VERIFICATION DISABLED!")
        logger.warning("NEVER USE IN PRODUCTION!")
        logger.warning("=" * 60)
    
    if not Config.REQUIRE_AICF and not Config.DEV_MODE:
        logger.warning("=" * 60)
        logger.warning("AICF NOT REQUIRED - This should only be used for testing!")
        logger.warning("In production, set ENA_REQUIRE_AICF=true")
        logger.warning("=" * 60)
    
    # Initialize RPC client
    rpc_client = AnimicaRPCClient(
        rpc_url=Config.RPC_URL,
        timeout=Config.RPC_TIMEOUT,
        max_retries=Config.RPC_MAX_RETRIES,
        retry_backoff=Config.RPC_RETRY_BACKOFF,
        circuit_breaker_threshold=Config.CIRCUIT_BREAKER_THRESHOLD,
        circuit_breaker_timeout=Config.CIRCUIT_BREAKER_TIMEOUT,
    )
    
    # Initialize database
    database = Database(Config.DB_PATH)
    
    # Initialize rate limiter
    rate_limiter = RateLimiter(
        requests_per_hour_address=Config.RATE_LIMIT_PER_ADDRESS,
        requests_per_hour_ip=Config.RATE_LIMIT_PER_IP,
    )
    
    # Initialize model registry
    model_registry = ModelRegistry(Config.MODELS_DIR)
    
    logger.info("ENA node started successfully")


@app.on_event("shutdown")
async def shutdown():
    """Clean up on shutdown."""
    global rpc_client
    
    logger.info("Shutting down ENA node...")
    
    if rpc_client:
        rpc_client.close()
    
    logger.info("ENA node shut down")


# Pydantic models for API

class PaymentInfo(BaseModel):
    """Payment information for a request."""
    mode: str = Field(..., description="Payment mode: per_call_tx or credit")
    payer: str = Field(..., description="Payer address")
    
    # Single transaction mode (if blockchain supports multi-output)
    tx_hash: Optional[str] = Field(None, description="Transaction hash (per_call_tx mode, single tx)")
    
    # Two transaction mode (separate service and AICF payments)
    tx_hash_service: Optional[str] = Field(None, description="Service payment tx hash")
    tx_hash_aicf: Optional[str] = Field(None, description="AICF payment tx hash")
    
    # Credit mode fields
    call_nonce: Optional[int] = Field(None, description="Call nonce (credit mode)")
    sig: Optional[str] = Field(None, description="Signature (credit mode)")
    
    @validator("mode")
    def validate_mode(cls, v):
        if v not in ["per_call_tx", "credit"]:
            raise ValueError("mode must be 'per_call_tx' or 'credit'")
        return v
    
    @validator("payer")
    def validate_payer(cls, v):
        if not validate_address(v):
            raise ValueError(f"Invalid address: {v}")
        return normalize_address(v)
    
    @validator("tx_hash")
    def validate_tx_hash(cls, v, values):
        if values.get("mode") == "per_call_tx" and v:
            if not validate_tx_hash(v):
                raise ValueError(f"Invalid tx_hash: {v}")
            return normalize_tx_hash(v)
        return v
    
    @validator("tx_hash_service")
    def validate_tx_hash_service(cls, v, values):
        if values.get("mode") == "per_call_tx" and v:
            if not validate_tx_hash(v):
                raise ValueError(f"Invalid tx_hash_service: {v}")
            return normalize_tx_hash(v)
        return v
    
    @validator("tx_hash_aicf")
    def validate_tx_hash_aicf(cls, v, values):
        if values.get("mode") == "per_call_tx" and v:
            if not validate_tx_hash(v):
                raise ValueError(f"Invalid tx_hash_aicf: {v}")
            return normalize_tx_hash(v)
        return v


class InferRequest(BaseModel):
    """Inference request."""
    prompt: str = Field(..., description="Input prompt", max_length=2000)
    model: Optional[str] = Field(None, description="Model name or alias")
    max_tokens: int = Field(100, description="Maximum tokens to generate", ge=1, le=500)
    temperature: float = Field(0.7, description="Sampling temperature", ge=0.0, le=1.0)
    payment: PaymentInfo = Field(..., description="Payment information")


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
    
    # AICF information
    aicf_address: str
    aicf_bp: int
    aicf_description: str = "AI Compute Fund - supports AI infrastructure"
    
    # Example breakdown
    example_call_cost: int
    example_aicf_cost: int
    example_service_cost: int


# Helper functions

def get_client_ip(request: Request) -> str:
    """Get client IP from request."""
    # Check X-Forwarded-For header first
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        # Take the first IP in the list
        return forwarded.split(",")[0].strip()
    
    # Fall back to client IP
    if request.client:
        return request.client.host
    
    return "unknown"


def calculate_cost(prompt_tokens: int, completion_tokens: int) -> int:
    """Calculate total cost for a request."""
    base_fee = Config.FEE_PER_CALL
    token_fee = completion_tokens * Config.FEE_PER_TOKEN
    return base_fee + token_fee


def verify_admin_token(authorization: Optional[str] = Header(None)) -> bool:
    """Verify admin token from Authorization header."""
    if not authorization:
        raise HTTPException(status_code=401, detail="Authorization required")
    
    # Expect: "Bearer <token>"
    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(status_code=401, detail="Invalid authorization format")
    
    token = parts[1]
    if token != Config.ADMIN_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid token")
    
    return True


# API Endpoints

@app.get("/v1/health")
async def health():
    """Health check endpoint."""
    return {
        "status": "ok",
        "version": "0.1.0",
        "dev_mode": Config.DEV_MODE,
    }


@app.get("/v1/pricing")
async def get_pricing() -> PricingInfo:
    """Get pricing information including AICF contribution details."""
    # Calculate example costs
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
    models = model_registry.list_models()
    aliases = model_registry.list_aliases()
    default_model = model_registry.get_default()
    
    return ModelsResponse(
        models=[
            ModelInfo(
                name=m.name,
                version=m.version,
                description=m.description,
                max_tokens=m.max_tokens,
            )
            for m in models
        ],
        aliases=aliases,
        default=default_model.name if default_model else "",
    )


@app.post("/v1/infer")
async def infer(request_data: InferRequest, request: Request) -> InferResponse:
    """
    Run inference with payment verification.
    
    Requires valid payment (either per-call transaction or credit).
    """
    request_id = str(uuid.uuid4())
    client_ip = get_client_ip(request)
    
    logger.info(
        f"Inference request: {request_id}",
        extra={
            "request_id": request_id,
            "payer": request_data.payment.payer,
            "mode": request_data.payment.mode,
            "ip": client_ip,
        }
    )
    
    try:
        # Rate limiting
        if not rate_limiter.check(request_data.payment.payer, client_ip):
            raise HTTPException(
                status_code=429,
                detail="Rate limit exceeded"
            )
        
        # Validate prompt
        if len(request_data.prompt) > Config.MAX_PROMPT_LENGTH:
            raise HTTPException(
                status_code=400,
                detail=f"Prompt too long (max {Config.MAX_PROMPT_LENGTH})"
            )
        
        # Get model
        model_name = request_data.model or Config.DEFAULT_MODEL
        model_info = model_registry.get_model(model_name)
        if not model_info:
            raise HTTPException(
                status_code=404,
                detail=f"Model not found: {model_name}"
            )
        
        # Verify payment
        amount_paid = 0
        service_paid = 0
        aicf_paid = 0
        aicf_required = 0
        aicf_explicit = False
        tx_hash = None
        tx_hash_service = None
        tx_hash_aicf = None
        
        if not Config.DEV_MODE:
            if request_data.payment.mode == "per_call_tx":
                # Per-call transaction mode with AICF contribution
                total_required = Config.MIN_FEE_PER_CALL
                
                # Calculate AICF split
                service_required, aicf_required = calculate_aicf_split(
                    total_required,
                    Config.AICF_BP,
                )
                
                # Get transaction hashes
                tx_hash = request_data.payment.tx_hash
                tx_hash_service = request_data.payment.tx_hash_service
                tx_hash_aicf = request_data.payment.tx_hash_aicf
                
                # Check if transactions already used (replay protection)
                if tx_hash and database.is_transaction_used(tx_hash):
                    raise HTTPException(
                        status_code=400,
                        detail="Transaction already used (replay protection)"
                    )
                if tx_hash_service and database.is_transaction_used(tx_hash_service):
                    raise HTTPException(
                        status_code=400,
                        detail="Service transaction already used (replay protection)"
                    )
                if tx_hash_aicf and database.is_transaction_used(tx_hash_aicf):
                    raise HTTPException(
                        status_code=400,
                        detail="AICF transaction already used (replay protection)"
                    )
                
                # Verify payment with AICF contribution
                try:
                    receipt = verify_payment_and_aicf(
                        rpc_client=rpc_client,
                        payer=request_data.payment.payer,
                        service_address=Config.SERVICE_ADDRESS,
                        aicf_address=Config.AICF_ADDRESS,
                        total_required=total_required,
                        aicf_bp=Config.AICF_BP,
                        tx_hash=tx_hash,
                        tx_hash_service=tx_hash_service,
                        tx_hash_aicf=tx_hash_aicf,
                        require_confirmed=False,  # Allow mempool
                    )
                except CircuitOpenError:
                    raise HTTPException(
                        status_code=503,
                        detail="RPC service unavailable - cannot verify payment"
                    )
                except AICFVerificationError as e:
                    # AICF contribution missing/insufficient
                    logger.error(
                        f"AICF verification failed: {e}",
                        extra={
                            "request_id": request_id,
                            "payer": request_data.payment.payer,
                            "error": str(e),
                        }
                    )
                    raise HTTPException(
                        status_code=400,
                        detail=f"AICF contribution missing/insufficient: {str(e)}"
                    )
                except TransactionVerificationError as e:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Payment verification failed: {str(e)}"
                    )
                
                # Extract amounts from receipt
                amount_paid = int(receipt.get("totalPaid", 0))
                service_paid = int(receipt.get("servicePaid", 0))
                aicf_paid = int(receipt.get("aicfPaid", 0))
                aicf_explicit = receipt.get("aicfExplicit", False)
                tx_hash = receipt.get("txHash")
                tx_hash_service = receipt.get("txHashService")
                tx_hash_aicf = receipt.get("txHashAicf")
                
                # Mark transactions as used (all of them to prevent replay)
                if tx_hash:
                    database.mark_transaction_used(
                        tx_hash=tx_hash,
                        payer=request_data.payment.payer,
                        amount=amount_paid,
                        request_id=request_id,
                    )
                if tx_hash_service:
                    database.mark_transaction_used(
                        tx_hash=tx_hash_service,
                        payer=request_data.payment.payer,
                        amount=service_paid,
                        request_id=request_id,
                    )
                if tx_hash_aicf:
                    database.mark_transaction_used(
                        tx_hash=tx_hash_aicf,
                        payer=request_data.payment.payer,
                        amount=aicf_paid,
                        request_id=request_id,
                    )
            
            elif request_data.payment.mode == "credit":
                # Credit mode
                # Estimate cost
                estimated_cost = Config.FEE_PER_CALL + (request_data.max_tokens * Config.FEE_PER_TOKEN)
                
                # Check and deduct credits
                if not database.deduct_credits(request_data.payment.payer, estimated_cost):
                    raise HTTPException(
                        status_code=402,
                        detail="Insufficient credits"
                    )
                
                amount_paid = estimated_cost
        
        else:
            # Dev mode - skip payment verification
            logger.warning(f"DEV MODE: Skipping payment verification for {request_id}")
            amount_paid = 0
        
        # Run inference
        if model_info.name not in inference_engines:
            inference_engines[model_info.name] = create_inference_engine(
                model_info.path,
                model_info.name,
            )
        
        engine = inference_engines[model_info.name]
        result = engine.infer(
            prompt=request_data.prompt,
            max_tokens=request_data.max_tokens,
            temperature=request_data.temperature,
        )
        
        # Calculate actual cost
        actual_cost = calculate_cost(
            result["usage"]["promptTokens"],
            result["usage"]["completionTokens"],
        )
        
        # Refund excess credits if in credit mode
        if request_data.payment.mode == "credit" and not Config.DEV_MODE:
            if actual_cost < amount_paid:
                refund = amount_paid - actual_cost
                database.add_credits(request_data.payment.payer, refund)
                amount_paid = actual_cost
        
        # Log request
        database.log_request(
            request_id=request_id,
            payer=request_data.payment.payer,
            model=model_info.name,
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
            model=model_info.name,
            usage=UsageInfo(**result["usage"]),
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
    except Exception as e:
        logger.error(f"Inference failed: {e}", exc_info=True)
        
        # Log failed request
        database.log_request(
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
            error=str(e),
        )
        
        raise HTTPException(status_code=500, detail=f"Internal error: {str(e)}")


# Admin endpoints

@app.post("/admin/set_default_model")
async def set_default_model(
    model_name: str,
    _: bool = Depends(verify_admin_token),
):
    """Set the default model (admin only)."""
    try:
        model_registry.set_default(model_name)
        return {"ok": True, "default": model_name}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.post("/admin/set_alias")
async def set_alias(
    alias: str,
    target: str,
    _: bool = Depends(verify_admin_token),
):
    """Set a model alias (admin only)."""
    try:
        model_registry.set_alias(alias, target)
        return {"ok": True, "alias": alias, "target": target}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.post("/admin/reload_models")
async def reload_models(_: bool = Depends(verify_admin_token)):
    """Reload models from directory (admin only)."""
    model_registry.reload()
    # Clear inference engines to force reload
    inference_engines.clear()
    return {"ok": True}


if __name__ == "__main__":
    import uvicorn
    
    uvicorn.run(
        app,
        host=Config.HOST,
        port=Config.PORT,
        log_level=Config.LOG_LEVEL.lower(),
    )
