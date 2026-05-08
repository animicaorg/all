# Exchange API Keys

API keys authenticate exchange API calls without a browser session. Generate them from
the exchange `Automation` page.

## Authentication

Send the key as a bearer token:

```bash
curl -H "Authorization: Bearer anm_live_..." \
  https://exchange.example.com/api/v1/me/balances
```

The gateway also accepts `X-API-Key`.

## Scopes

- `read`: balances, open orders, order history, and trade history.
- `trade`: place and cancel orders.

API keys cannot create new API keys or access transfer endpoints.

## Place An Order

```bash
curl -X POST https://exchange.example.com/api/v1/orders \
  -H "Authorization: Bearer anm_live_..." \
  -H "Content-Type: application/json" \
  -d '{
    "symbol": "LTC-ANM",
    "side": "buy",
    "type": "LIMIT",
    "price": 1,
    "quantity": 0.01,
    "clientOrderId": "client-123"
  }'
```

## Revoke Keys

Revoke compromised or unused keys from the `Automation` page. The raw secret is only
shown once when it is generated.
