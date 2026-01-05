"""
Webhooks Router for Stripe and PayPal
"""

from fastapi import APIRouter, Request, HTTPException, Header, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from billing_service.config import settings
from billing_service.database import get_db
from billing_service.models import CreditLedger, PaymentIntent
from billing_service.services import ledger
from decimal import Decimal
import logging
import hmac
import hashlib
import json

router = APIRouter()
logger = logging.getLogger(__name__)


def verify_stripe_signature(payload: bytes, signature: str, secret: str) -> bool:
    """Verify Stripe webhook signature"""
    if not secret or not signature:
        return False
    
    try:
        # Stripe signature format: t=timestamp,v1=signature
        elements = signature.split(',')
        timestamp = None
        signatures = []
        
        for element in elements:
            key, value = element.split('=')
            if key == 't':
                timestamp = value
            elif key.startswith('v'):
                signatures.append(value)
        
        if not timestamp or not signatures:
            return False
        
        # Compute expected signature
        signed_payload = f"{timestamp}.{payload.decode('utf-8')}"
        expected_sig = hmac.new(
            secret.encode('utf-8'),
            signed_payload.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
        
        # Compare with provided signatures (constant-time comparison)
        return any(hmac.compare_digest(expected_sig, sig) for sig in signatures)
    except Exception as e:
        logger.error(f"Stripe signature verification failed: {e}")
        return False


@router.post("/stripe")
async def stripe_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
    stripe_signature: str = Header(None, alias="Stripe-Signature")
):
    """Handle Stripe webhooks"""
    
    payload = await request.body()
    
    # Verify webhook signature if secret is configured
    if settings.STRIPE_WEBHOOK_SECRET:
        if not stripe_signature:
            raise HTTPException(status_code=401, detail="Missing signature")
        
        if not verify_stripe_signature(payload, stripe_signature, settings.STRIPE_WEBHOOK_SECRET):
            raise HTTPException(status_code=401, detail="Invalid signature")
    
    # Parse event
    try:
        event = json.loads(payload)
    except Exception as e:
        logger.error(f"Failed to parse webhook payload: {e}")
        raise HTTPException(status_code=400, detail="Invalid payload")
    
    event_type = event.get("type")
    event_data = event.get("data", {}).get("object", {})
    
    logger.info(f"Received Stripe webhook: {event_type}")
    
    # Handle different event types
    try:
        if event_type == "checkout.session.completed":
            # Handle successful checkout
            session = event_data
            user_id = session.get("client_reference_id")
            amount = Decimal(session.get("amount_total", 0)) / 100  # Convert cents to dollars
            
            if user_id and amount > 0:
                # Add credits to user account
                await ledger.add_credits(
                    db, 
                    user_id, 
                    amount,
                    source="stripe",
                    reference_id=session.get("id")
                )
                logger.info(f"Added {amount} credits to user {user_id}")
        
        elif event_type == "customer.subscription.created":
            # Handle subscription creation
            subscription = event_data
            customer_id = subscription.get("customer")
            logger.info(f"Subscription created for customer {customer_id}")
            # TODO: Create subscription record
        
        elif event_type == "customer.subscription.deleted":
            # Handle subscription cancellation
            subscription = event_data
            logger.info(f"Subscription deleted: {subscription.get('id')}")
            # TODO: Update subscription status
        
        elif event_type == "invoice.payment_succeeded":
            # Handle recurring subscription payment
            invoice = event_data
            customer_id = invoice.get("customer")
            amount = Decimal(invoice.get("amount_paid", 0)) / 100
            logger.info(f"Invoice paid: {amount} for customer {customer_id}")
            # TODO: Add recurring credits
        
        elif event_type == "invoice.payment_failed":
            # Handle failed payment
            invoice = event_data
            logger.warning(f"Invoice payment failed: {invoice.get('id')}")
            # TODO: Notify user and suspend service
        
        else:
            logger.info(f"Unhandled Stripe event type: {event_type}")
    
    except Exception as e:
        logger.error(f"Error handling Stripe webhook: {e}", exc_info=True)
        # Return 200 to prevent retries for unrecoverable errors
        return {"received": True, "error": str(e)}
    
    return {"received": True}


@router.post("/paypal")
async def paypal_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db)
):
    """Handle PayPal webhooks"""
    
    payload = await request.body()
    headers = dict(request.headers)
    
    # TODO: Implement PayPal webhook signature verification
    # PayPal uses certificates for verification
    # https://developer.paypal.com/docs/api-basics/notifications/webhooks/notification-messages/
    
    try:
        event = json.loads(payload)
    except Exception as e:
        logger.error(f"Failed to parse webhook payload: {e}")
        raise HTTPException(status_code=400, detail="Invalid payload")
    
    event_type = event.get("event_type")
    resource = event.get("resource", {})
    
    logger.info(f"Received PayPal webhook: {event_type}")
    
    # Handle different event types
    try:
        if event_type == "PAYMENT.SALE.COMPLETED":
            # Handle successful payment
            sale_id = resource.get("id")
            amount = Decimal(resource.get("amount", {}).get("total", 0))
            custom_id = resource.get("custom_id")  # User ID passed during payment
            
            if custom_id and amount > 0:
                await ledger.add_credits(
                    db,
                    custom_id,
                    amount,
                    source="paypal",
                    reference_id=sale_id
                )
                logger.info(f"Added {amount} credits to user {custom_id}")
        
        elif event_type == "BILLING.SUBSCRIPTION.CREATED":
            # Handle subscription creation
            subscription_id = resource.get("id")
            logger.info(f"PayPal subscription created: {subscription_id}")
            # TODO: Create subscription record
        
        elif event_type == "BILLING.SUBSCRIPTION.CANCELLED":
            # Handle subscription cancellation
            subscription_id = resource.get("id")
            logger.info(f"PayPal subscription cancelled: {subscription_id}")
            # TODO: Update subscription status
        
        else:
            logger.info(f"Unhandled PayPal event type: {event_type}")
    
    except Exception as e:
        logger.error(f"Error handling PayPal webhook: {e}", exc_info=True)
        return {"received": True, "error": str(e)}
    
    return {"received": True}
