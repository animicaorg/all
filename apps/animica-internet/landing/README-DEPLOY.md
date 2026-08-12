# Deploying the Animica Internet landing page + installers

The Animica Internet desktop app takes over the download slot previously used
by Studio Qt on animica.org. Everything ships through the existing Astro
website pipeline: files under `website/public/` are copied verbatim into the
build, and the built site is rsynced to the live web root.

## 1. Drop the installers into the website source

Copy the CI-built installers (from the release workflow's `downloads/`
aggregate) into the website's public tree. These exact names are what the
landing page links to:

```
website/public/internet/
├── index.html                                   # this landing page (landing/index.html)
├── animica-internet-windows-x64-setup.exe       # Windows Inno Setup installer
├── animica-internet-macos.dmg                   # macOS disk image (unsigned; .zip fallback OK)
├── animica-internet-linux-x86_64.AppImage       # Linux AppImage
└── manifest.json                                # optional: CI downloads/manifest.json
```

```bash
mkdir -p /root/animica/website/public/internet
cp /root/animica/apps/animica-internet/landing/index.html /root/animica/website/public/internet/
# then copy the three installers from the GitHub Release / CI artifacts
```

Note: `website/public/studio/` (Studio Qt) can stay in place for old links,
but the homepage stops pointing at it (step 3).

## 2. Rebuild and rsync to the live root

Same flow as every other animica.org change:

```bash
cd /root/animica/website
pnpm build                                   # astro build -> dist/
rsync -av dist/ /var/www/animica.org/        # live root
```

After this, the page is live at https://animica.org/internet/ and the
installers at:

- https://animica.org/internet/animica-internet-windows-x64-setup.exe
- https://animica.org/internet/animica-internet-macos.dmg
- https://animica.org/internet/animica-internet-linux-x86_64.AppImage

Back up the previous live state first if in doubt (precedent:
`/root/site-backups`).

## 3. Replace the homepage download section

`website/src/pages/index.astro` lines ~349-368 currently advertise
"Animica Studio — agentic code studio" with `/studio/...` download buttons.
Replace that `<section class="aicf-section">` block so it advertises the
Animica Internet app instead:

- Kicker: `New · Desktop app`
- Heading: `Animica Internet — a browser for the .anm web`
- Copy: standalone Win/Mac/Linux browser for .anm registry sites only
  (no clearnet), built-in wallet, name reservation paid in ANM, one-click
  publish/serve.
- Buttons (keep `aicf-btn` classes and `download` attrs):
  - `/internet/animica-internet-windows-x64-setup.exe` — Windows (installer)
  - `/internet/animica-internet-macos.dmg` — macOS (.dmg)
  - `/internet/animica-internet-linux-x86_64.AppImage` — Linux (AppImage)
  - optional `aicf-ghost-btn` → `/internet/` for the full landing page
- Fine-print line: macOS is unsigned (right-click → Open); Linux AppImage
  needs `chmod +x` before running.

Rebuild + rsync again after editing (step 2).

## 4. nginx

Nothing is required: `/internet/` is served by the generic static root of the
`animica.org.conf` server block (`/etc/nginx/sites-enabled/animica.org.conf`),
same as any other page.

Optional: mirror the `/wallet` caching pattern so a re-released installer
propagates quickly while HTML never goes stale. Add alongside the existing
`/wallet` blocks:

```nginx
# Landing HTML must never be cached (new releases surface immediately).
location ~ ^/internet/(index\.html?|)$ {
    root /var/www/animica.org;
    index index.html;
    try_files $uri $uri/ /internet/index.html;
    include /etc/nginx/snippets/animica-security-headers.conf;
    add_header Cache-Control "no-cache, no-store, must-revalidate" always;
    add_header Pragma "no-cache" always;
}
# Installers: short cache so a re-upload under the same name propagates
# within minutes.
location ~ ^/internet/.*\.(exe|dmg|zip|AppImage)$ {
    root /var/www/animica.org;
    include /etc/nginx/snippets/animica-security-headers.conf;
    add_header Cache-Control "public, max-age=300, must-revalidate" always;
}
```

Then `nginx -t && systemctl reload nginx`.

The cross-site upgrade banner is injected by the existing
`anm-upgrade-inject.conf` sub_filter on the literal `</body>` tag — the
landing page keeps that tag, so no extra wiring is needed.

## 5. Smoke test

```bash
curl -sI https://animica.org/internet/ | head -5
curl -sI https://animica.org/internet/animica-internet-linux-x86_64.AppImage | grep -i 'content-length\|content-type'
curl -s  https://animica.org/internet/ | grep -c '</body>'   # banner injection point present
```

Download each installer once and launch it on the matching OS (macOS:
right-click → Open; Linux: `chmod +x` first).
