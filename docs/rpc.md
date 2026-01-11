# Walletd JSON-RPC

`walletd` is a lightweight background service started by the Qt wallet. It exposes a
JSON-RPC 2.0 API over HTTP bound to `127.0.0.1` only.

## Connection details

- **Default URL:** `http://127.0.0.1:17834`
- **Port override:** set `ANIMICA_WALLETD_PORT`
- **Auth token:** generated on first run and stored in the Qt wallet app data directory as
  `walletd.token` (permissions `0600`).
- **Auth header:** `Authorization: Bearer <token>` (alternatively `X-Auth-Token`).

Example:

```bash
curl -X POST http://127.0.0.1:17834 \
  -H "Authorization: Bearer $WALLETD_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"walletd.health","params":{}}'
```

## Methods

### `walletd.health`

Health check.

Response:

```json
{"status":"ok"}
```

### `walletd.version`

Returns the walletd version.

Response:

```json
{"version":"0.1.0"}
```

### `walletd.getStatus`

Returns basic service status.

Response:

```json
{
  "node_running": false,
  "pid": 12345,
  "rpc_url": "http://127.0.0.1:17834",
  "last_error": null
}
```

### `walletd.getLogsTail`

Returns the last N lines of the walletd log.

Params:

```json
{"lines": 200}
```

Response:

```json
{"lines": ["2024-06-01 12:00:00 [INFO] ..."]}
```
