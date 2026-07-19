# Animica 9.0.0 — ship runbook (remaining credentialed steps)

Everything buildable/deployable without secrets is DONE and live (see bottom).
The steps below need a token or are consensus-sensitive, so run them yourself.

## 1. Publish to PyPI (needs the PyPI token at run time)
Artifacts already built + gated (`scripts/release_gate.sh` passed):
```
ls python/dist/animica-9.0.0*        # wheel + sdist, 8.4M / 7.6M
/root/animica/.venv/bin/twine upload python/dist/animica-9.0.0-py3-none-any.whl python/dist/animica-9.0.0.tar.gz
# paste the PyPI API token when prompted (username __token__)
```
Verify: `pip index versions animica` shows 9.0.0.

## 2. Push to GitHub (needs a GitHub token; recipe in memory animica_github_publishing.md)
Working tree is dirty with unrelated churn — stage ONLY the 9.0.0 surface:
```
cd /root/animica
git add python/ consensus/ core/chain/block_import.py core/network_params.py \
        scripts/release_gate.sh docs/gpu-studios-9.0.0.md ops/systemd/animica-iou-settle.* \
        apps/animica-marketplace SHIP_9.0.0.md sites/animica.dev-studios
git commit -m "Animica 9.0.0 — GPU Studios (video/audio/Blender render farm) + on-chain IOU settlement @50,000"
# push to animicaorg/all with an inline token helper (LFS breaks URL-embedded tokens):
git -c credential.helper='!f(){ echo username=x-access-token; echo password=<GHP>; }; f' \
    push origin HEAD:refs/heads/release/9.0.0
git tag v9.0.0 && git -c credential.helper='...' push origin v9.0.0
# advance origin/main via the plumbing-overlay + core trim per memory animica_github_publishing.md
```

## 3. Flip the upgrade banner (do AFTER step 1 so `pip install -U animica` gets 9.0.0)
Staged file is ready and syntax-checked:
```
cp /root/animica/sites/animica.dev-studios/anm-upgrade-banner-9.0.0.js /var/www/animica.dev/anm-upgrade-banner.js
# bump ?v=852 -> ?v=900 in BOTH snippets + the hardcoded homepage tag:
sed -i 's/anm-upgrade-banner.js?v=852/anm-upgrade-banner.js?v=900/' \
   /etc/nginx/snippets/anm-upgrade-inject.conf /etc/nginx/snippets/anm-upgrade-inject-proxy.conf \
   /var/www/animica.dev/index.html
nginx -t && nginx -s reload
```
(The /upgrade guide is already updated to 9.0.0.)

## 4. Upgrade the canonical node to 9.0.0 (consensus — do before block 50,000)
The 9.0.0 consensus code is already in the `/root/animica` bind-mount. The fork is
INERT until block 50,000 AND until the treasury posts the first settlement anchor, so
this is a safe rolling restart with ~1,500 blocks (~a day) of runway at height 48,509:
```
docker restart animica-mainnet-node
# watch it resync its head cleanly; height/head unchanged, no divergence expected
```

## 5. (Optional, when ready to actually pay IOUs) enable the settlement worker
Only meaningful at/after block 50,000, and only once miners set payout addresses:
```
cp ops/systemd/animica-iou-settle.{service,timer} /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now animica-iou-settle.timer
# inspect first:  animica settle status   |   ANM_SETTLE_POST= python scripts/iou_settlement_worker.py  (dry run)
```

## Rotate credentials after publishing
Per standing note: PyPI token, GitHub token → rotate once used.

---
## Already DONE + verified live (no action needed)
- Marketplace app (:4950) rebuilt + restarted — advertises all 17 media kinds.
- Studio pages live: https://animica.dev/{video,audio,render}/ (HTTP 200).
- Homepage spliced (nav + ⚡ GPU Studios chip + #gpu section + eco cards).
- nginx big-body routes live (verified a 12 MB upload streamed through the edge).
- openapi.json + llms.txt document the 5 studio endpoints; sw.js → anmdev-v4.
- /upgrade guide updated to 9.0.0.
- Wheel+sdist built, release_gate PASSED, spec/params.yaml now vendored into the wheel.
- 156 python tests green (media + consensus); marketplace tsc clean; full render-farm
  orchestration smoke green end-to-end.
- Adversarial review (6 dims × refuter panels): all confirmed findings fixed.
- Backups: /root/site-backups/9.0.0-<ts>/ (index.html, banner, openapi, llms, sw, nginx).

## Post-mortem addenda (2026-07-19) — MANDATORY for 9.0.1+ deploys
The 9.0.0 deploy caused a 5-hour marketplace outage (Jul 17 21:26 → Jul 18 02:32 CEST,
~4,500 systemd restarts) and a 30-hour unapplied-nginx window. Two process rules:

1. **Never `next build` in the live app dir while the service runs.** The build wipes
   `.next` first, so `next start` crash-loops against a missing build and every miner
   poll gets a 502 at the edge (the pre-9.0.1 miner client hard-exited on that — it
   killed the fleet's poll loops). Always:
   ```
   cd apps/animica-marketplace && cp -a .next .next.bak && systemctl stop animica-marketplace
   npm run build && rm -rf .next.bak || { rm -rf .next && mv .next.bak .next; }
   systemctl start animica-marketplace
   ```
2. **Applying `deploy/animica.dev-marketplace.nginx.conf` is a release step, not an
   optional one.** Copy the blocks into sites-available, `nginx -t && systemctl reload
   nginx`, then curl-probe every new body-size limit (a >24MB POST must NOT 413).
   The 9.0.0 large-body blocks sat unapplied Jul 18 02:20 → Jul 19 04:55; every large
   result upload in that window died at the edge.
