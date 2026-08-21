# Installing the x402 nginx location set (deliberate runbook step)

**Nothing in this directory is live.** These are repo example files; the
cutover below is a human-approved deployment action, done together with the
systemd cutover in `../systemd/` (the nginx target `:8742` only exists once
`src/server.js` is what `animica-x402.service` runs).

## What deployment replaces

`/etc/nginx/sites-enabled/animica.dev.conf` currently carries a simple
catch-all that fronts the DEV entry:

```nginx
# x402 payment gateway (animica-x402.service :4656; /x402/ prefix stripped)
location ^~ /x402/ {
    proxy_pass http://127.0.0.1:4656/;     # demo-server.js, prefix stripped
    ...
    proxy_read_timeout 90s;
}
```

Deploying this location set **REPLACES that block** — remove it, or the
older `^~ /x402/` will shadow/compete with the new exact-match locations.
Two differences to be aware of during the cutover:

* **Upstream changes** from `127.0.0.1:4656` (demo) to `127.0.0.1:8742`
  (production gateway).
* **The `/x402` prefix is no longer stripped** (`proxy_pass` has no URI
  part). The gateway routes canonical `/x402/...` paths natively (and would
  also accept stripped ones — `src/products/registry.js` tries both — but
  the un-stripped form keeps `resource.url`, logs and docs consistent).

## Steps

```sh
# 1. http{}-context rate-limit zones (REQUIRED — limit_req_zone is not
#    allowed inside server{}). COPY THE FILE; do not retype the rates.
#
#    This step used to inline the zone lines here, and that copy went stale:
#    it said catalog rate=5r/s (the real value is 300r/s, sized against a
#    measured 115 req/s agent sweep) and it omitted the x402_probe and
#    x402_crawl_gate zones entirely — which every location block references,
#    so `nginx -t` fails outright and no reload is possible. The rates carry
#    their own measurement notes in the file; read those before changing them,
#    and see test/nginx-rate-floors.test.js for the floors they must not drop
#    below.
cp nginx/animica-x402-zones.conf /etc/nginx/conf.d/animica-x402-zones.conf

# 2. the location set:
cp nginx/animica-dev-x402.conf /etc/nginx/snippets/animica-dev-x402.conf

# 3. edit /etc/nginx/sites-enabled/animica.dev.conf:
#      - DELETE the old `location ^~ /x402/ { proxy_pass http://127.0.0.1:4656/; … }`
#      - ADD inside the server{} block:  include /etc/nginx/snippets/animica-dev-x402.conf;

# 4. validate + reload (validate FIRST — a bad reload takes animica.dev down):
nginx -t && systemctl reload nginx
```

## Post-cutover smoke

```sh
curl -s  https://animica.dev/x402 | python3 -m json.tool | head   # catalog
curl -si https://animica.dev/x402/qrng/draw | head -5             # 402 + PAYMENT-REQUIRED
curl -si https://animica.dev/x402/healthz                          # 200
curl -si https://animica.dev/x402/metrics | head -1                # 404 (loopback-only)
for i in 1 2 3 4 5 6 7 8; do curl -so /dev/null -w "%{http_code} " \
  https://animica.dev/x402/chain/blocks?from=0; done; echo         # 402s then 429 (bulk zone)
```

## What is deliberately NOT exposed

* The **facilitator** (`127.0.0.1:8743`) — `/verify`, `/settle`, `/readyz`,
  `/metrics` are loopback-only. No nginx location references it, ever.
* The gateway's **`/metrics`** — it lives at the bare `/metrics` path on
  loopback; this set only proxies `/x402/*` and `/.well-known/x402`, and
  `/x402/metrics` is a 404 inside the gateway.

## Deployment-day gotchas (from this host's history)

* `deploy.sh`-style site rebuilds have clobbered hand-edited nginx snippets
  before — keep the include line and the zones file out of any generated
  config, and re-check `nginx -T | grep x402` after any site redeploy.
* The demo entry keeps working throughout: until step 3's reload, /x402/
  traffic still reaches `:4656`; after it, `:8742`. There is no window
  where both answer, and rolling back = restoring the old location block
  and `systemctl reload nginx`.
