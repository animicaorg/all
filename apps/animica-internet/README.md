# Animica Internet

A standalone desktop browser for the **.anm Animica Internet** — Windows, macOS, and
Linux. Built with PySide6 + QtWebEngine.

Animica Internet is **.anm-only**: it browses sites published to the Animica network
and nothing else. There is no clearnet access — `http://`, `https://`, and every other
non-`anm://` scheme are refused at the URL-handling layer, so a page can never smuggle
you (or your wallet) out to the regular web.

## Features

- **.anm-only browsing** — an `anm://` custom scheme handler resolves `.anm` names
  through the on-chain registry and loads content addressed by CID. No clearnet,
  no trackers, no surprises.
- **Built-in wallet with fail-closed approval** — the wallet backend (from the
  `animica` package, loaded lazily) is never exposed to page content directly. Every
  action that spends funds or signs anything pops a native approval dialog; if the
  dialog is bypassed, closed, or errors, the request is **denied by default**.
- **Name reservation** — reserve `.anm` names from inside the browser. Reservation
  fees are paid to the Animica Foundation.
- **Publish & serve** — package a local directory as a `.anm` site, pin it, and
  announce it to the network, all from the built-in publisher panel.

## Security model: `anm://` + CID verification

1. The `anm://` scheme is registered with QtWebEngine **before** the application
   starts, so it is a first-class scheme with its own sandboxed handler — not a
   proxy or an HTTP rewrite.
2. A `.anm` name is resolved via the on-chain name registry to a **content
   identifier (CID)**. The resolver fetches the content bytes and verifies that
   their hash matches the CID before a single byte reaches the renderer. Content
   that does not match its CID is dropped, not rendered.
3. Because addressing is content-based, whoever serves the bytes is irrelevant —
   a malicious or compromised host can refuse to serve, but it cannot serve you
   something other than what the name owner published.
4. Wallet access from pages goes through a narrow bridge with explicit,
   per-request user approval. The bridge is **fail-closed**: anything unexpected
   (unknown method, malformed request, missing approval) results in a rejection.

## Run from source

Requires Python 3.10+.

```sh
cd apps/animica-internet
pip install -e .
animica-internet
```

The GUI needs a real display (or a CI runner with one). On a headless box you can
still verify the install with the smoke check, which exercises imports and the
entry point without opening a window:

```sh
python -m animica_internet.main --smoke
# prints: animica-internet smoke: OK
```

## Building installers

Installers are produced by the GitHub Actions release workflow (modelled on the
GUI miner pipeline: a 3-OS PyInstaller matrix — PyInstaller cannot cross-compile,
so each artifact is built on its native runner):

- **Windows** (`windows-latest`) — onedir build wrapped with Inno Setup:
  `animica-internet-windows-x64-setup.exe`
- **macOS** (`macos-latest`) — `.app` bundle shipped as
  `animica-internet-macos.dmg` (or `.zip`)
- **Linux** (`ubuntu-latest`) — `animica-internet-linux-x86_64.AppImage`

To cut a release, push a release tag for this app (or trigger the workflow
manually via *workflow_dispatch* from the Actions tab). The final job aggregates
the artifacts, writes a `manifest.json`, and attaches everything to a GitHub
Release; installers are then published to the download slot on animica.org.

Note for packagers: the PyInstaller spec must collect `PySide6.QtWebEngineCore`,
the `QtWebEngineProcess` helper binary, and its resources (via `collect_all` /
`collect_data_files`), plus a Qt runtime hook to fix plugin paths in the frozen
app — QtWebEngine does not survive a naive one-file build.

## Development

```sh
pip install -e .
python -m pytest tests/          # unit tests (headless-safe)
python -m animica_internet.main --smoke
```

Package layout: `animica_internet/` — `main` (entry point), `app`, `scheme`
(anm:// handler), `resolver` (name → CID → verified bytes), `config`, `wallet` +
`wallet_ui` (fail-closed approval), `bridge` (page ↔ wallet), `panels`,
`registry_client`, `names` (reservation), `serve` (publishing).
