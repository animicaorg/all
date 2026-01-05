"""
Webhooks Router for Stripe and PayPal
"""

from fastapi import APIRouter, Request, HTTPException, Header
from billing_service.config import settings
import logging
import hmac
import hashlib

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post("/stripe")
async def stripe_webhook(
    request: Request,
    stripe_signature: str = Header(None, alias="Stripe-Signature")
):
    """Handle Stripe webhooks"""
    
    payload = await request.body()
    
    # Verify webhook signature
    if settings.STRIPE_WEBHOOK_SECRET and stripe_signature:
        # TODO: Implement proper Stripe signature verification
        # stripe.Webhook.construct_event(payload, stripe_signature, settings.STRIPE_WEBHOOK_SECRET)
        pass
    
    # Parse event
    try:
        import json
        event = json.loads(payload)
    except Exception as e:
        logger.error(f"Failed to parse webhook payload: {e}")
        raise HTTPException(status_code=400, detail="Invalid payload")
    
    # Handle event
    event_type = event.get("type")
    
    logger.info(f"Received Stripe webhook: {event_type}")
    
    # TODO: Implement event handlers
    # if event_type == "customer.subscription.created":
    #     handle_subscription_created(event["data"]["object"])
    # elif event_type == "invoice.payment_succeeded":
    #     handle_payment_succeeded(event["data"]["object"])
    # ...
    
    return {"received": True}


@router.post("/paypal")
async def paypal_webhook(request: Request):
    """Handle PayPal webhooks"""
    
    payload = await request.body()
    
    # TODO: Verify PayPal webhook signature
    
    try:
        import json
        event = json.loads(payload)
    except Exception as e:
        logger.error(f"Failed to parse webhook payload: {e}")
        raise HTTPException(status_code=400, detail="Invalid payload")
    
    event_type = event.get("event_type")
    
    logger.info(f"Received PayPal webhook: {event_type}")
    
    # TODO: Implement event handlers
    
    return {"received": True}
