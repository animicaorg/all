# Installing the Linux Qt wallet (.deb)

## Quick install (Ubuntu 24.04 / Debian 12+)

```bash
curl -L -o animica-wallet.deb https://animica.org/wallet/animica-wallet-linux.deb
sudo apt install ./animica-wallet.deb
animica-wallet
```

`apt install ./file.deb` resolves the package's dependencies
(`libqt6core6`, `libqt6gui6`, `libqt6widgets6`, `libqt6network6`,
`libqt6sql6`, `libssl3`, `python3.12`) and installs them as needed.

## On Ubuntu 22.04 / Debian 11

The bundled Python venv expects Python 3.12, which isn't in the default
22.04 repositories. Add the deadsnakes PPA first:

```bash
sudo add-apt-repository ppa:deadsnakes/ppa
sudo apt update
sudo apt install python3.12
sudo apt install ./animica-wallet.deb
```

On Debian 11 you can grab Python 3.12 from Debian backports.

## On older distros

If your distro is older than the above, the bundled Python won't load
(the venv's pyvenv.cfg pins to 3.12.3). Workarounds:

1. Build from source: `git clone https://github.com/animicaorg/all && cd all/wallet-qt && ./scripts/release-linux.sh` (regenerates a venv matching your system Python).
2. Or skip the desktop wallet and use the CLI: `pip install animica` (works with Python 3.10+).

## Uninstall

```bash
sudo apt remove animica-wallet
```

Wallet data lives in `~/.local/share/Animica/AnimicaWallet/` and is
preserved across reinstall. Delete it manually if you want a fresh
state.

## What it installs

- `/usr/bin/animica-wallet` — the Qt GUI (1.3 MB)
- `/usr/lib/x86_64-linux-gnu/animica-wallet/node/venv/` — bundled
  Python 3.12 venv with the `animica` CLI (~50 MB)
- `/usr/share/applications/animica-wallet.desktop` — Activities entry
- `/usr/share/icons/hicolor/*/apps/animica-wallet.png` — icons

The bundled CLI is invoked when you create or sign through the GUI;
the wallet works without it if you only need to view balances.

## Verify

```bash
sha256sum animica-wallet.deb
curl -s https://animica.org/wallet/animica-wallet-linux.sha256
```

The two values should match.

## Why isn't it signed with a GPG key?

A signed apt repository would let you add Animica as a regular package
source (`apt update / apt install animica-wallet`). That requires
running a repo server with GPG keys and `Release.gpg` files. Until
that's set up, the .deb ships unsigned — apt will warn `--- unverified
package` but install proceeds.
