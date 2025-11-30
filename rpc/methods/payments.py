"""
Animica Payment Webhook Handler

Handles payment confirmations from Stripe, PayPal, and other payment processors.
Verifies webhook signatures, records purchases, and mints tokens on-chain.

Webhook Events:
- Stripe: payment_intent.succeeded, charge.succeeded
- PayPal: PAYMENT.CAPTURE.COMPLETED, BILLING.SUBSCRIPTION.CREATED

Integration:
1. Payment processor (wallet) initiates purchase via marketplace RPC methods
2. User completes payment in payment gateway
3. Gateway sends webhook confirmation to this endpoint
4. Endpoint verifies signature and updates treasury state
5. Tokens are minted to user address on-chain

Error Handling:
- Invalid signatures reject webhook (403)
- Duplicate webhook IDs are idempotent
- Network errors trigger webhook retries on processor side
"""

import hashlib
import hmac
import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Literal, Optional

from consensus.interfaces import PolicyProvider
from execution.interfaces import StateDB

logger = logging.getLogger(__name__)


# ============================================================================
# TYPE DEFINITIONS
# ============================================================================


@dataclass
class PaymentWebhook:
    """Received webhook from payment processor"""

    provider: Literal["stripe", "paypal"]
    webhook_id: str  # Unique webhook ID (prevents duplicates)
    timestamp: str  # ISO 8601 timestamp
    event_type: str  # e.g., "payment_intent.succeeded"

    # Payment details
    order_id: str  # Idempotency key / order reference
    user_address: str  # Recipient wallet address (0x-prefixed)
    anm_quantity: float  # ANM tokens to mint
    usd_amount: float  # Payment amount in USD
    price_per_anm: float  # Price used for purchase

    # Verification
    signature: str  # HMAC signature from processor
    raw_body: str  # Original request body for signature verification


@dataclass
class PurchaseRecord:
    """Purchase recorded on-chain"""

    id: str  # Unique purchase ID
    timestamp: str  # ISO 8601 timestamp
    user_address: str  # Recipient wallet address
    anm_quantity: float  # Tokens minted
    usd_amount: float  # Payment amount
    price_per_anm: float  # Price per token
    provider: str  # Payment provider (stripe, paypal)
    order_id: str  # Original order reference
    status: Literal["pending", "confirmed", "minted", "failed"] = "pending"
    transaction_hash: Optional[str] = None  # On-chain tx hash
    receipt_url: Optional[str] = None  # Payment receipt from processor


@dataclass
class PaymentError(Exception):
    """Payment processing error"""

    code: Literal[
        "invalid_signature",
        "duplicate_webhook",
        "invalid_address",
        "insufficient_treasury",
        "minting_failed",
        "state_update_failed",
    ]
    message: str
    details: dict = None


@dataclass
class PaymentResponse:
    """Response to webhook"""

    success: bool
    webhook_id: str
    order_id: str
    message: str
    transaction_hash: Optional[str] = None
    timestamp: Optional[str] = None


# ============================================================================
# WEBHOOK PROCESSING
# ============================================================================


async def process_payment_webhook(
    webhook: PaymentWebhook,
    state_db: StateDB,
    policy_provider: PolicyProvider,
    stripe_webhook_secret: Optional[str] = None,
    paypal_webhook_secret: Optional[str] = None,
) -> PaymentResponse:
    """
    Process a payment webhook from Stripe or PayPal.

    Args:
        webhook: PaymentWebhook with event details
        state_db: State database for persisting purchases
        policy_provider: Policy provider for treasury limits
        stripe_webhook_secret: Stripe webhook signing secret
        paypal_webhook_secret: PayPal webhook signing secret

    Returns:
        PaymentResponse with success status and transaction hash

    Raises:
        PaymentError: If webhook is invalid, payment fails, or on-chain call fails
    """

    # Step 1: Verify webhook signature
    try:
        _verify_webhook_signature(
            webhook=webhook,
            stripe_secret=stripe_webhook_secret,
            paypal_secret=paypal_webhook_secret,
        )
    except PaymentError as e:
        logger.warning(f"Invalid webhook signature: {e.message}")
        raise

    # Step 2: Validate payment details
    try:
        _validate_payment(webhook)
    except PaymentError as e:
        logger.error(f"Invalid payment details: {e.message}")
        raise

    # Step 3: Check for duplicate webhook
    try:
        existing = await state_db.get_purchase_by_webhook_id(webhook.webhook_id)
        if existing:
            logger.info(
                f"Duplicate webhook {webhook.webhook_id}, returning cached response"
            )
            return PaymentResponse(
                success=True,
                webhook_id=webhook.webhook_id,
                order_id=webhook.order_id,
                message="Payment already processed",
                transaction_hash=existing.get("transaction_hash"),
                timestamp=existing.get("timestamp"),
            )
    except Exception as e:
        logger.debug(f"Could not check for duplicate: {e}, proceeding")

    # Step 4: Verify treasury limits (policy check)
    try:
        policy = policy_provider.get_active_policy()
        max_purchase_usd = policy.get("max_purchase_usd", 100_000)

        if webhook.usd_amount > max_purchase_usd:
            raise PaymentError(
                code="insufficient_treasury",
                message=f"Purchase amount {webhook.usd_amount} exceeds limit {max_purchase_usd}",
                details={"limit": max_purchase_usd, "requested": webhook.usd_amount},
            )
    except PaymentError:
        raise
    except Exception as e:
        logger.warning(f"Could not verify policy limits: {e}, proceeding")

    # Step 5: Record purchase in database
    purchase_record = PurchaseRecord(
        id=webhook.webhook_id,
        timestamp=webhook.timestamp,
        user_address=webhook.user_address,
        anm_quantity=webhook.anm_quantity,
        usd_amount=webhook.usd_amount,
        price_per_anm=webhook.price_per_anm,
        provider=webhook.provider,
        order_id=webhook.order_id,
        status="confirmed",
        receipt_url=_build_receipt_url(webhook),
    )

    try:
        await state_db.save_purchase(asdict(purchase_record))
    except Exception as e:
        logger.error(f"Failed to save purchase: {e}")
        raise PaymentError(
            code="state_update_failed",
            message=f"Could not save purchase to database: {e}",
        )

    # Step 6: Mint tokens on-chain (TODO: replace with actual on-chain call)
    try:
        tx_hash = await _mint_tokens_on_chain(
            user_address=webhook.user_address,
            anm_quantity=webhook.anm_quantity,
            order_id=webhook.order_id,
        )

        # Update purchase with transaction hash
        purchase_record.transaction_hash = tx_hash
        purchase_record.status = "minted"
        await state_db.save_purchase(asdict(purchase_record))

        logger.info(
            f"Payment processed: {webhook.order_id} → "
            f"{webhook.user_address} ({webhook.anm_quantity} ANM) "
            f"tx={tx_hash}"
        )

        return PaymentResponse(
            success=True,
            webhook_id=webhook.webhook_id,
            order_id=webhook.order_id,
            message="Payment processed and tokens minted",
            transaction_hash=tx_hash,
            timestamp=datetime.now().isoformat(),
        )

    except Exception as e:
        logger.error(f"Failed to mint tokens on-chain: {e}")

        # Mark purchase as failed but keep record for debugging
        purchase_record.status = "failed"
        try:
            await state_db.save_purchase(asdict(purchase_record))
        except:
            pass

        raise PaymentError(
            code="minting_failed",
            message=f"Could not mint tokens on-chain: {e}",
        )


def _verify_webhook_signature(
    webhook: PaymentWebhook,
    stripe_secret: Optional[str],
    paypal_secret: Optional[str],
) -> None:
    """Verify webhook signature from payment processor"""

    if webhook.provider == "stripe":
        if not stripe_secret:
            raise PaymentError(
                code="invalid_signature",
                message="Stripe webhook secret not configured",
            )

        # Stripe signature format: t=timestamp,v1=signature
        # See: https://stripe.com/docs/webhooks/signatures
        expected_sig = hmac.new(
            stripe_secret.encode(),
            webhook.raw_body.encode(),
            hashlib.sha256,
        ).hexdigest()

        if not hmac.compare_digest(expected_sig, webhook.signature):
            raise PaymentError(
                code="invalid_signature",
                message="Invalid Stripe webhook signature",
            )

    elif webhook.provider == "paypal":
        if not paypal_secret:
            raise PaymentError(
                code="invalid_signature",
                message="PayPal webhook secret not configured",
            )

        # PayPal signature verification requires:
        # 1. receiver_id
        # 2. transmission_id
        # 3. transmission_time
        # 4. cert_url (to fetch signing cert)
        # 5. Reconstructed body + signature verification
        # See: https://developer.paypal.com/docs/api-basics/notifications/webhooks/

        # For now, basic HMAC verification
        expected_sig = hmac.new(
            paypal_secret.encode(),
            webhook.raw_body.encode(),
            hashlib.sha256,
        ).hexdigest()

        if not hmac.compare_digest(expected_sig, webhook.signature):
            raise PaymentError(
                code="invalid_signature",
                message="Invalid PayPal webhook signature",
            )

    else:
        raise PaymentError(
            code="invalid_signature",
            message=f"Unknown payment provider: {webhook.provider}",
        )


def _validate_payment(webhook: PaymentWebhook) -> None:
    """Validate payment details"""

    # Check address format (Ethereum address)
    if not webhook.user_address.startswith("0x") or len(webhook.user_address) != 42:
        raise PaymentError(
            code="invalid_address",
            message=f"Invalid user address: {webhook.user_address}",
            details={"address": webhook.user_address},
        )

    # Check quantities/amounts
    if webhook.anm_quantity <= 0:
        raise PaymentError(
            code="invalid_signature",
            message=f"Invalid ANM quantity: {webhook.anm_quantity}",
        )

    if webhook.usd_amount <= 0:
        raise PaymentError(
            code="invalid_signature",
            message=f"Invalid USD amount: {webhook.usd_amount}",
        )

    if webhook.price_per_anm <= 0:
        raise PaymentError(
            code="invalid_signature",
            message=f"Invalid price per ANM: {webhook.price_per_anm}",
        )

    # Verify amount matches quantity * price (with small tolerance)
    expected_amount = webhook.anm_quantity * webhook.price_per_anm
    tolerance = expected_amount * 0.01  # 1% tolerance for rounding

    if abs(webhook.usd_amount - expected_amount) > tolerance:
        logger.warning(
            f"Amount mismatch: {webhook.usd_amount} vs "
            f"{webhook.anm_quantity} * {webhook.price_per_anm} = {expected_amount}"
        )


def _build_receipt_url(webhook: PaymentWebhook) -> str:
    """Build receipt URL for payment processor"""

    if webhook.provider == "stripe":
        # Stripe: receipts.stripe.com/<order_id>
        return f"https://receipts.stripe.com/{webhook.order_id}"

    elif webhook.provider == "paypal":
        # PayPal: sandbox/live receipt URL
        return f"https://www.paypal.com/cgi-bin/webscr?cmd=_view-a-trans&id={webhook.order_id}"

    return ""


async def _mint_tokens_on_chain(
    user_address: str,
    anm_quantity: float,
    order_id: str,
) -> str:
    """
    Mint ANM tokens to user address on-chain.

    TODO: Replace with actual on-chain minting call:
    1. Load treasury contract
    2. Call mint(user_address, anm_quantity)
    3. Wait for transaction confirmation
    4. Return transaction hash

    Returns:
        Transaction hash
    """

    # For now, return mock transaction hash
    import secrets

    tx_hash = "0x" + secrets.token_hex(32)

    logger.info(
        f"[MOCK] Minting {anm_quantity} ANM to {user_address} "
        f"(order={order_id}) → tx={tx_hash}"
    )

    return tx_hash


# ============================================================================
# RPC METHODS
# ============================================================================


async def process_stripe_webhook(
    body: dict,
    signature: str,
    state_db: StateDB,
    policy_provider: PolicyProvider,
    stripe_webhook_secret: str,
) -> PaymentResponse:
    """
    Process Stripe webhook event.

    RPC Method: `marketplace_processStripeWebhook`

    Params:
        body (dict): Stripe webhook event body
        signature (str): Signature from Stripe-Signature header

    Returns:
        PaymentResponse: Confirmation of payment processing

    Errors:
        -32000: Invalid signature
        -32001: Invalid payment details
        -32002: On-chain minting failed
    """

    # Extract payment details from Stripe event
    event = body
    event_type = event.get("type")

    if event_type == "payment_intent.succeeded":
        payment_intent = event.get("data", {}).get("object", {})

        webhook = PaymentWebhook(
            provider="stripe",
            webhook_id=event.get("id"),
            timestamp=datetime.fromtimestamp(event.get("created")).isoformat(),
            event_type=event_type,
            order_id=payment_intent.get("client_secret"),
            user_address=payment_intent.get("metadata", {}).get("user_address"),
            anm_quantity=float(
                payment_intent.get("metadata", {}).get("anm_quantity", 0)
            ),
            usd_amount=float(payment_intent.get("amount")) / 100,  # Stripe uses cents
            price_per_anm=float(
                payment_intent.get("metadata", {}).get("price_per_anm", 0)
            ),
            signature=signature,
            raw_body=json.dumps(body),
        )

        return await process_payment_webhook(
            webhook=webhook,
            state_db=state_db,
            policy_provider=policy_provider,
            stripe_webhook_secret=stripe_webhook_secret,
        )

    else:
        raise PaymentError(
            code="invalid_signature",
            message=f"Unsupported Stripe event type: {event_type}",
        )


async def process_paypal_webhook(
    body: dict,
    signature: str,
    state_db: StateDB,
    policy_provider: PolicyProvider,
    paypal_webhook_secret: str,
) -> PaymentResponse:
    """
    Process PayPal webhook event.

    RPC Method: `marketplace_processPayPalWebhook`

    Params:
        body (dict): PayPal webhook event body
        signature (str): Signature from PayPal header

    Returns:
        PaymentResponse: Confirmation of payment processing

    Errors:
        -32000: Invalid signature
        -32001: Invalid payment details
        -32002: On-chain minting failed
    """

    # Extract payment details from PayPal event
    event_type = body.get("event_type")

    if event_type == "PAYMENT.CAPTURE.COMPLETED":
        resource = body.get("resource", {})

        webhook = PaymentWebhook(
            provider="paypal",
            webhook_id=body.get("id"),
            timestamp=body.get("create_time"),
            event_type=event_type,
            order_id=resource.get("id"),
            user_address=resource.get("custom_id"),
            anm_quantity=float(
                resource.get("supplementary_data", {}).get("anm_quantity", 0)
            ),
            usd_amount=float(resource.get("amount", {}).get("value", 0)),
            price_per_anm=float(
                resource.get("supplementary_data", {}).get("price_per_anm", 0)
            ),
            signature=signature,
            raw_body=json.dumps(body),
        )

        return await process_payment_webhook(
            webhook=webhook,
            state_db=state_db,
            policy_provider=policy_provider,
            paypal_webhook_secret=paypal_webhook_secret,
        )

    else:
        raise PaymentError(
            code="invalid_signature",
            message=f"Unsupported PayPal event type: {event_type}",
        )


# ============================================================================
# TESTING FIXTURES
# ============================================================================

FIXTURE_STRIPE_WEBHOOK = {
    "id": "evt_test_12345",
    "object": "event",
    "type": "payment_intent.succeeded",
    "created": 1700000000,
    "data": {
        "object": {
            "id": "pi_test_67890",
            "object": "payment_intent",
            "amount": 150000,  # $1500 in cents
            "client_secret": "pi_test_67890_secret",
            "metadata": {
                "user_address": "0x1234567890123456789012345678901234567890",
                "anm_quantity": "1000",
                "price_per_anm": "1.50",
            },
        }
    },
}

FIXTURE_PAYPAL_WEBHOOK = {
    "id": "WH-12345",
    "event_type": "PAYMENT.CAPTURE.COMPLETED",
    "create_time": "2024-11-24T12:00:00Z",
    "resource": {
        "id": "PAY-12345",
        "amount": {
            "value": "1500.00",
            "currency_code": "USD",
        },
        "custom_id": "0x1234567890123456789012345678901234567890",
        "supplementary_data": {
            "anm_quantity": "1000",
            "price_per_anm": "1.50",
        },
    },
}
