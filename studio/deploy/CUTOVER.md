# studio.animica.org — Cutover Runbook

Flip `studio.animica.org` from the **old hosted-terminal broker** to the **new
static landing site** (SDK + RPC only).

> **This REMOVES the hosted terminal.** The old site proxied a per-user noVNC
> desktop / shell session served by the `studio-host` broker on
> `127.0.0.1:8123` — an authenticated, shell-capable agent reachable from the
> public internet. After this cutover there is **no noVNC, no terminal, no
> shell embed** on `studio.animica.org`. Studio becomes a static landing page
> plus `/rpc` and `/v1` API proxies. The Python SDK (`pip install animica`) is
> the only way to run compute.

> Steps marked **[LIVE — needs operator confirmation]** mutate the running
> server (nginx, systemd). Read them, then run them deliberately. Everything
> before the first such step is read-only.

---

## 0. What you are deploying

| Item | Path |
|---|---|
| New static site root | `/root/animica/studio/web/` (`index.html`, `llms.txt`) |
| New nginx vhost | `/root/animica/studio/deploy/nginx-studio.animica.org.conf` |
| Live nginx vhost target | `/etc/nginx/sites-available/studio.animica.org.conf` |
| Old broker service (to stop) | `studio-host.service` → `node src/server.js` on `127.0.0.1:8123` |

Upstreams the new vhost proxies (confirm these match your box):
- `/rpc` → `http://127.0.0.1:8545` (node JSON-RPC: `state.*` / `chain.*` / `miner.*` / `aicf.*`)
- `/v1`  → `http://127.0.0.1:8787` (AI compute broker / `animica-pool-api`)

If your broker listens on a different local port, edit the `proxy_pass` in
`nginx-studio.animica.org.conf` **before** installing it.

---

## 1. Pre-flight (read-only)

```bash
# Confirm the new artifacts exist and the HTML is present.
ls -l /root/animica/studio/web/index.html /root/animica/studio/web/llms.txt
ls -l /root/animica/studio/deploy/nginx-studio.animica.org.conf

# See the current live vhost (what we are replacing).
sudo cat /etc/nginx/sites-available/studio.animica.org.conf

# See what the old broker is (and whether it is running).
systemctl status studio-host --no-pager || true
sudo ss -ltnp | grep -E ':8123|:8545|:8787' || true
```

---

## 2. Back up the current live vhost  **[LIVE — needs operator confirmation]**

```bash
sudo cp -a /etc/nginx/sites-available/studio.animica.org.conf \
           /etc/nginx/sites-available/studio.animica.org.conf.bak.$(date +%Y%m%d-%H%M%S)

# verify the backup landed
ls -l /etc/nginx/sites-available/studio.animica.org.conf.bak.*
```

---

## 3. Install the new vhost  **[LIVE — needs operator confirmation]**

```bash
sudo cp /root/animica/studio/deploy/nginx-studio.animica.org.conf \
        /etc/nginx/sites-available/studio.animica.org.conf

# Ensure it is enabled (skip if the symlink already exists).
sudo ln -sf /etc/nginx/sites-available/studio.animica.org.conf \
            /etc/nginx/sites-enabled/studio.animica.org.conf
```

---

## 4. Validate, then reload nginx  **[LIVE — needs operator confirmation]**

```bash
# MUST print "syntax is ok" / "test is successful". If not, STOP and fix.
sudo nginx -t

# Reload (no dropped connections). Use restart only if reload misbehaves.
sudo systemctl reload nginx
```

If `nginx -t` fails, the live config is unchanged on disk only if you have not
yet reloaded. To revert at this point, copy the backup from step 2 back over
`studio.animica.org.conf` and re-run `sudo nginx -t && sudo systemctl reload nginx`.

---

## 5. Smoke-test the new site (read-only)

```bash
# Landing page (expect 200 + HTML).
curl -sSI https://studio.animica.org/ | head -n 5
curl -sS  https://studio.animica.org/ | grep -i "Animica Studio" | head -n 1

# llms.txt is served and plain text (expect 200, content-type text/plain).
curl -sSI https://studio.animica.org/llms.txt | head -n 5

# Confirm the security header is present.
curl -sSI https://studio.animica.org/ | grep -i "content-security-policy"

# RPC proxy reaches the node (expect a JSON-RPC response, not a 502).
curl -sS https://studio.animica.org/rpc \
  -H 'content-type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"chain.getHead","params":[]}' | head -c 400; echo

# Confirm the OLD terminal is gone (expect NOT a noVNC/login page).
curl -sS https://studio.animica.org/ | grep -i -E "novnc|vnc|terminal|xterm" || echo "OK: no terminal markers in page"
```

---

## 6. Stop and disable the old :8123 broker  **[LIVE — needs operator confirmation]**

The broker is no longer referenced by nginx, but leave nothing listening that
could be re-exposed. This permanently retires the hosted terminal.

```bash
# Stop it now and prevent it from starting on boot.
sudo systemctl disable --now studio-host

# Verify it is dead and nothing remains on 127.0.0.1:8123.
systemctl is-active studio-host || echo "studio-host: stopped"
sudo ss -ltnp | grep ':8123' || echo "OK: nothing listening on :8123"
```

Optional hard removal (only once you are sure you will not roll back):

```bash
sudo systemctl mask studio-host        # belt-and-suspenders: block any re-enable
# To fully remove the unit later:
#   sudo rm -f /etc/systemd/system/studio-host.service && sudo systemctl daemon-reload
```

> Do **not** re-run `studio-host/deploy/go-live.sh` — it rewrites this vhost to
> proxy `127.0.0.1:8123` again and re-enables the broker, undoing this cutover.

---

## 7. Rollback (if you must restore the old terminal)

```bash
# Restore the backed-up vhost (pick the timestamp you saved in step 2).
sudo cp /etc/nginx/sites-available/studio.animica.org.conf.bak.<TIMESTAMP> \
        /etc/nginx/sites-available/studio.animica.org.conf
sudo nginx -t && sudo systemctl reload nginx

# Bring the broker back (re-exposes the public shell — do this only deliberately).
sudo systemctl unmask studio-host 2>/dev/null || true
sudo systemctl enable --now studio-host
```

---

## Done

`studio.animica.org` now serves the static Studio landing site with `/rpc` and
`/v1` API proxies, and the noVNC / hosted-terminal broker on `127.0.0.1:8123` is
stopped and disabled.
