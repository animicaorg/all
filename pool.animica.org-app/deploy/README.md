# Deploy runbook — pool.animica.org/app (rig rental marketplace)

Go-live steps. Steps 4–8 touch live infrastructure (DB, secrets, the
production pool service, nginx) — do them deliberately, sandbox-first.

## 1. Provision Postgres
```
POOL_RENTAL_DB_PASSWORD=$(openssl rand -hex 24) bash deploy/provision-db.sh
```
Copy the printed `DATABASE_URL`.

## 2. Secrets / env
```
cp .env.example /etc/animica/pool-rental.env
# Fill: DATABASE_URL, SESSION_SECRET (openssl rand -hex 32),
# POOL_RENTAL_SHARED_SECRET (openssl rand -hex 24), NOWPAYMENTS_* (sandbox
# first), SMTP_PASS (from /etc/animica/chat.env). Keep NOWPAYMENTS_SANDBOX=true.
```
Add the SAME secret to the pool so it accepts our rental calls:
```
echo "POOL_RENTAL_SHARED_SECRET=<same value>" >> /etc/animica/pool.env
```

## 3. Build + migrate
```
cd /root/animica/pool.animica.org-app
npm ci
npx prisma migrate deploy    # or: npx prisma db push (first cut)
npm run build
npx prisma db seed
```

## 4. Restart the pool (activates the /api/rental/* endpoints + redirect)
```
systemctl restart animica-pool.service
```
The rental endpoints return 503 until POOL_RENTAL_SHARED_SECRET is set.

## 5. systemd units for the app + worker
```
cp deploy/pool-rental.service deploy/pool-rental-worker.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now pool-rental.service pool-rental-worker.service
```

## 6. nginx
Merge `deploy/nginx-app.conf` into the `pool.animica.org` server block, then:
```
nginx -t && systemctl reload nginx
```

## 7. Build pool-web (adds the Rent / Rent out entry pages)
```
cd /root/animica/pool-web && npm run build
# deploy dist/ to /var/www/pool.animica.org/
```

## 8. Verify (sandbox) — see plan §Verification / task #3b
Owner claim → list → renter rents (sandbox invoice) → webhook confirms →
worker activates (assert renter credited in animica_pool.db worker_balances) →
window ends → owner paid 95% → offline test → pro-rata refund.

## Flip to production
Set `NOWPAYMENTS_SANDBOX=false` and real NOWPayments creds in
`/etc/animica/pool-rental.env`, then `systemctl restart pool-rental*`.
