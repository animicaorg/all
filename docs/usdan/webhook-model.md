# Webhook Model

Webhook processor path: `services/usdan-api/src/services/webhookService.ts`.

## Ingestion steps

1. Validate HMAC signature from provider (`verifyWebhookSignature`).
2. Parse event envelope.
3. Idempotency lookup via `provider + eventId` (`webhook_deliveries`).
4. Route event type:
- inbound settled -> purchase settlement transition
- payout settled -> redemption completion transition
5. Persist delivery status: `RECEIVED -> PROCESSED|FAILED`.

## Endpoint

- `POST /webhooks/modern-treasury`
- Signature headers accepted: `x-signature` or `modern-treasury-signature`
