# Flathub feasibility — Animica Internet (`org.animica.Internet`)

Verdict: **feasible, but the highest-effort target in this kit — do it after
Homebrew/winget/AUR/Docker.** Nothing here has been submitted or built with
flatpak-builder (not available on this host); this is an honest scoping doc
plus a skeleton manifest to start from.

## Why it is MORE feasible than first assumed

The devreg recon flagged "PySide6/QtWebEngine apps are heavy" — true, but the
key discovery from the app's own `pyproject.toml`
(`apps/animica-internet/pyproject.toml`) improves the picture:

* `animica-internet` depends ONLY on `PySide6>=6.6`, `PySide6-Addons`,
  `requests`, `certifi`. It does **not** depend on the multi-GB `animica`
  package — no torch, no AI stack. The wallet/signing logic it needs is
  embedded in the app package itself.
* It is MIT-licensed, has a proper console entry point
  (`animica-internet = animica_internet.main:main`), and a stable app id is
  already in use upstream: the macOS bundle id is `org.animica.internet`, so
  `org.animica.Internet` is the natural Flatpak id (Flathub requires you to
  prove control of animica.org for that id, or use an
  `io.github.animicaorg.*` id instead — animica.org is controlled, so this is
  fine; verification is a DNS TXT record or a `/.well-known` file).

## The real costs

1. **QtWebEngine via PySide6 wheels.** Two viable routes:
   * **BaseApp route (recommended):** build on `org.kde.Platform` +
     `io.qt.PySide.BaseApp` (exists for Qt 6.7/6.8 branches), which ships
     PySide6 including QtWebEngine prebuilt. Cuts build time from hours to
     minutes. Constraint: your PySide6 version is pinned to the BaseApp
     branch.
   * **pip route:** vendor PySide6 wheels with flatpak-pip-generator.
     Flathub's offline-build rule means every wheel must be listed as a
     pinned source; PySide6+Addons wheels are ~500 MB. Works, but reviewers
     prefer the BaseApp.
2. **Offline, reproducible builds.** Flathub sandboxes the build with no
   network: every Python dep (`requests`, `certifi`, `charset-normalizer`,
   `idna`, `urllib3`) must be a pinned wheel source generated with
   `flatpak-pip-generator` (or `python -m pip download` + manual sources).
   With only 5 leaf deps this is genuinely small.
3. **From-source expectation.** Repacking the PyInstaller AppImage is NOT
   acceptable to Flathub review; the manifest below builds the Python package
   from the animicaorg/all git tree (subdir `apps/animica-internet`).
4. **Sandbox friction to test on real hardware:** QtWebEngine needs
   `--share=ipc --socket=wayland --socket=fallback-x11 --device=dri`;
   the app writes wallet state under `~/.animica` (needs a
   `--filesystem=~/.animica` hole or better, migrate to
   `$XDG_DATA_HOME` inside the sandbox — check `animica_internet/` code for
   the actual path before submission). anm:// URL handling should be declared
   so links open the app.
5. **Submission mechanics** (all human): fork `flathub/flathub`, branch from
   `new-pr`, add the manifest dir, open PR against `new-pr`, pass review
   (typically 1-3 weeks, reviewers WILL comment on the id, permissions, and
   offline sources).

## Skeleton manifest (starting point, NOT build-tested)

`org.animica.Internet.yaml`:

```yaml
app-id: org.animica.Internet
runtime: org.kde.Platform
runtime-version: "6.8"
base: io.qt.PySide.BaseApp
base-version: "6.8"
sdk: org.kde.Sdk
command: animica-internet
finish-args:
  - --share=network
  - --share=ipc
  - --socket=wayland
  - --socket=fallback-x11
  - --socket=pulseaudio
  - --device=dri
  # wallet/profile state — verify actual path in animica_internet before review
  - --filesystem=~/.animica:create
cleanup-commands:
  - /app/cleanup-BaseApp.sh
modules:
  # generate with: flatpak-pip-generator --requirements-file=requirements.txt
  # for: requests certifi charset-normalizer idna urllib3
  - python3-requirements.json
  - name: animica-internet
    buildsystem: simple
    build-commands:
      # the app lives in a monorepo subdir; build from there
      - cd apps/animica-internet && pip3 install --prefix=/app --no-deps --no-build-isolation .
    sources:
      - type: git
        url: https://github.com/animicaorg/all.git
        # pin the exact commit of the release being packaged:
        tag: anmnet-v0.1.1
  - name: metadata
    buildsystem: simple
    build-commands:
      - install -Dm644 org.animica.Internet.desktop -t /app/share/applications
      - install -Dm644 org.animica.Internet.metainfo.xml -t /app/share/metainfo
      - install -Dm644 icon-256.png /app/share/icons/hicolor/256x256/apps/org.animica.Internet.png
    sources:
      - type: dir
        path: assets
```

Still to create before a PR: `.desktop` file, AppStream
`org.animica.Internet.metainfo.xml` (with OARS rating + screenshots — Flathub
hard-requires these), 256px icon (exists in the repo at
`apps/animica-internet/assets/`, verify size), and the generated
`python3-requirements.json`.

## Recommendation

Ship Homebrew cask + winget + AUR + Docker first (days). Budget Flathub as a
separate 1-2 week effort including review round-trips, done on a Linux
desktop with flatpak-builder where the QtWebEngine sandbox behavior can
actually be exercised. The CLI (`animica`) itself is NOT a Flathub candidate
(Flathub is for GUI apps; CLI-only submissions are discouraged).
