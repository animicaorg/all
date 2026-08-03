# AUR submission notes — `animica`

## What's here

* `PKGBUILD` — builds the real PyPI sdist `animica-9.0.8.tar.gz`
  (sha256 `429a4c270f33847cb1d0954dae2dea2e13b3830199a55eddb76e14d36e1fc712`,
  verified against the downloaded artifact) with the standard
  `python-build`/`python-installer` flow. The sdist's custom `hatch_build.py`
  vendors the node packages (`rpc/`, `consensus/`, `core/`, `p2p/`, ...) into
  the wheel, so the built package really contains the full node.
* `.SRCINFO` — **hand-written** to mirror the PKGBUILD.

## Validation status (be honest with yourself before pushing)

* `makepkg` is NOT available on the Ubuntu host where these files were
  generated, so the PKGBUILD is **inspection-only**: it was not built with
  makepkg, and `.SRCINFO` was not machine-generated. On a real Arch system
  (or an `archlinux:base-devel` container) run:
  ```sh
  makepkg --printsrcinfo > .SRCINFO   # regenerate — do not trust the hand copy
  makepkg -s                          # full build test
  namcap PKGBUILD animica-9.0.8-1-any.pkg.tar.zst
  ```
* The dependency choice IS empirically grounded: a Python 3.12 venv with
  exactly the 13 `depends=` packages (pip equivalents) plus `--no-deps
  animica==9.0.8` boots `python -m rpc` on mainnet and serves `/healthz`.
  Upstream's full metadata additionally demands the AI stack (torch etc.);
  those are `optdepends=` here because the code imports them lazily. `pip
  check` inside the installed environment will complain — pacman won't.
* Arch package-name mapping to double-check on an Arch box (`pacman -Si ...`):
  `python-cbor2`, `python-prometheus-client`, and `uvicorn` live in
  [extra]; `python-fastapi` and `python-typer` are in [extra] as of 2026 but
  were community packages historically. If any has been dropped to AUR since,
  move it to makedepends/optdepends or vendor it.

## AUR account requirements (human-only, cannot be automated)

1. An AUR account (https://aur.archlinux.org) with an **SSH public key**
   registered in the account settings. There is no API/token path — AUR
   pushes are SSH-only (`ssh://aur@aur.archlinux.org/animica.git`).
2. First push claims the package name:
   ```sh
   git clone ssh://aur@aur.archlinux.org/animica.git   # empty repo = name free
   cp PKGBUILD .SRCINFO animica/
   cd animica && git add PKGBUILD .SRCINFO
   git commit -m "animica 9.0.8-1: initial import"
   git push origin master
   ```
3. Naming: `animica` (application suite) was chosen over `python-animica`
   (library convention). If a Trusted User asks, offer to rename; the PKGBUILD
   header documents the rationale.
4. On each release: bump `pkgver`, reset `pkgrel=1`, update `sha256sums`,
   regenerate `.SRCINFO`, push.

## Known rough edges

* `animica-fastpow` (native SHA3 miner fast path) is a base pip dep upstream
  but has no Arch package; the code degrades to a pure-Python fallback, so it
  is simply omitted. Mining hashrate from an AUR install will be poor until a
  `python-animica-fastpow` package exists (its sdist needs only a C compiler).
* The package installs ~40 MB of Python code (arch `any`); no AI models or
  binaries are bundled.
