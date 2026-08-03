# animicaorg/homebrew-animica

Homebrew tap for the [Animica](https://animica.org) post-quantum L1 blockchain.

This repository is meant to be published as **`animicaorg/homebrew-animica`**
on GitHub (Homebrew resolves `brew tap animicaorg/animica` to that repo name).

## What's in the tap

| Package | Type | Installs |
|---|---|---|
| `animica` | Formula | The `animica` CLI from the PyPI sdist (v9.0.8) — node, wallet, miner, contracts, AI/ENA jobs |
| `animica-internet` | Cask | Animica Internet — `.anm`-only desktop browser with built-in wallet (macOS, Apple Silicon) |
| `animica-wallet` | Cask | Animica Wallet — Qt desktop wallet (macOS, Apple Silicon) |

## Usage

```sh
brew tap animicaorg/animica
brew install animicaorg/animica/animica            # CLI (large: pulls the AI stack)
brew install --cask animicaorg/animica/animica-internet
brew install --cask animicaorg/animica/animica-wallet
```

## Maintainer notes

* **Formula/animica.rb** pins the real PyPI sdist for 9.0.8
  (`sha256 429a4c270f33847cb1d0954dae2dea2e13b3830199a55eddb76e14d36e1fc712`,
  verified against the downloaded artifact). It deliberately uses the
  *pip-install-from-sdist* pattern instead of a full `resource` graph: the
  upstream package has ~60 base dependencies including torch, so a faithful
  resource list is impractical. The tradeoff (network during install, pip —
  not Homebrew — verifies transitive deps) is documented in the formula
  header. This is fine for a tap; it would be rejected from homebrew-core.
* **On every release**: bump `url` + `sha256` in the formula (grab the new
  digest from `https://pypi.org/pypi/animica/json`), and re-pin both cask
  sha256s (upstream currently overwrites unversioned download URLs — see the
  warnings inside each cask).
* **Casks are arm64-only** — the published dmgs contain arm64 Mach-O binaries
  with no x86_64 slice.
* **animica-internet is currently BLOCKED**: its download URL
  (`https://animica.org/internet/animica-internet-macos.dmg`) 404s because the
  `/internet` directory fell out of the live web root in the 2026-07-29 site
  redeploy. Restore the directory (the exact artifacts, checksums verified,
  are in the server backup `site-backups/animica.org-20260729-044756/internet/`)
  before publishing that cask.
* Apps are ad-hoc signed and not notarized; both casks print Gatekeeper
  caveats.

## Publishing this repo (human steps)

1. Create the GitHub repo `animicaorg/homebrew-animica` (public).
2. Copy the contents of this directory (`README.md`, `Formula/`, `Casks/`) to
   the repo root and push.
3. Sanity check from any Mac:
   `brew tap animicaorg/animica && brew audit --tap animicaorg/animica`
   (`brew audit`/`brew style` could not be run where these files were
   generated — no macOS/Homebrew/ruby available; files were checked by hand.)
