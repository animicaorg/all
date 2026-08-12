# hello-api

The minimal Animica Python Cloud function: one module-level `main(request)` that returns
JSON-serializable data. That is the entire runtime ABI — no framework, no decorators, no
imports required.

## What it demonstrates

* The `def main(request)` entrypoint shape (`request` is the parsed JSON body of a POST, or
  the query-string parameters of a GET).
* Returning a plain dict, which becomes the JSON response of the public endpoint.
* The free tier: this function is public, requires no auth and sets no per-call surcharge, so
  anonymous callers can hit it inside the free-tier allowance.

## Deploy configuration (set by `scripts/cloud-examples.ts`)

| setting | value |
| --- | --- |
| entrypoint | `main` |
| timeout | 10 000 ms |
| memory | 128 MB |
| capabilities | none |
| per-call surcharge | 0 nANM |

## Invoke

```bash
curl -s https://animica.dev/api/cloud/v1/fn/examples/hello-api \
  -H 'content-type: application/json' \
  -d '{"name": "Ada"}'
```

Response:

```json
{"greeting": "Hello, Ada!", "echo": {"name": "Ada"}, "runtime": "python3.12"}
```

The `x-animica-request-id`, `x-animica-cost-nanm` and `x-animica-status` response headers
carry the execution's identity and exact metered cost.
