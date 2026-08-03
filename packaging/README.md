# Animica OS-packaging kit

Generated 2026-08-03. Validated files + human submission instructions for
distributing Animica through OS package managers. **Nothing in here has been
pushed or submitted anywhere** — every target needs a human account.

| Dir | Target | Status |
|---|---|---|
| `homebrew-tap/` | GitHub repo `animicaorg/homebrew-animica` (complete repo content) | Formula verified against real PyPI sdist sha256; casks carry verified sha256s; `animica-internet` cask BLOCKED on restoring the `/internet` web dir |
| `winget/` | microsoft/winget-pkgs PR for Animica Internet 0.1.0 | Manifests ready (schema 1.12.0, real sha256, Inno Setup confirmed); BLOCKED on same 404 + URL immutability — see `winget/submission-steps.md` |
| `aur/` | AUR package `animica` | PKGBUILD + hand-written .SRCINFO; makepkg unavailable here → regenerate .SRCINFO on Arch before pushing; needs SSH-keyed AUR account |
| `docker/` | Slim public node image | Built and smoke-tested locally (see docker/README.md); ~verified `python -m rpc` boots with 36 pinned deps |
| `flathub/` | Flathub `org.animica.Internet` | Feasibility doc + skeleton manifest only |

## Key verified facts (2026-08-03)

* PyPI `animica` 9.0.8 sdist sha256
  `429a4c270f33847cb1d0954dae2dea2e13b3830199a55eddb76e14d36e1fc712`
  (downloaded + recomputed).
* Animica Internet 0.1.0 artifacts (built 2026-07-27, checksums recomputed and
  matching their published `.sha256` sidecars):
  * macOS dmg `bd93436d4eb9ab938a0637de931f5d4130036baec23d6c66d484261472ecd4b3`
    (arm64-only, `AnimicaInternet.app`, bundle id `org.animica.internet`)
  * Windows x64 Inno Setup exe
    `65fee7ff83c7593204bf000fb468d996f88ae196446917ab53cd070ebae82d9b`
  * Linux AppImage `621d89672406156d7086175327601f572c5974da70f3c350c8f2e21411f3d5e0`
* Animica Wallet macOS dmg (LIVE at animica.org/wallet, build v0.2.6 arm64)
  `898b56d9c06d46d4ec91098d3076c8e1f8ebed4b5df5d02e9befa4fec773e3b0`.
* Node P2P port **30333/tcp** (mainnet; QUIC 30334/udp, WS 30335/tcp).

## THE blocker to fix first

`https://animica.org/internet/` (page + all three installers) **404s since
the 2026-07-29 site redeploy** — the artifacts survive only in
`/root/site-backups/animica.org-20260729-044756/internet/`. Until a human
restores them (ideally under versioned paths, or as GitHub release assets on
the existing-but-assetless `anmnet-v0.1.0`/`v0.1.1` tags), the
`animica-internet` cask and the whole winget submission are dead in the
water. The wallet cask and everything else are unaffected.
