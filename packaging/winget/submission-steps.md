# Submitting Animica Internet to winget (microsoft/winget-pkgs)

Status: manifests are ready under
`manifests/a/Animica/AnimicaInternet/0.1.0/` (schema **1.12.0**, current as of
Aug 2026), but submission is **BLOCKED** until the two installer-URL issues
below are fixed. Every step here needs a human with a GitHub account —
winget-pkgs has no API-only submission path.

## Verified facts baked into the manifests

| Fact | Value | How verified |
|---|---|---|
| Installer tech | **Inno Setup** | CI build script `apps/animica-internet/packaging/build_windows.ps1` runs `iscc` (choco `innosetup`) |
| Silent flags | `/VERYSILENT /SUPPRESSMSGBOXES /NORESTART` (winget applies these automatically for `InstallerType: inno`) | Inno Setup standard; no custom `[Setup]` overrides present |
| ProductCode | `{9C4E7A2D-5B31-4F8E-B6D0-ANMINTERNET1}_is1` | Inno `AppId` in the generated .iss + Inno's `_is1` uninstall-key convention |
| Scope | per-user (`PrivilegesRequired=lowest`, override dialog allowed) | same .iss |
| SHA256 | `65FEE7FF83C7593204BF000FB468D996F88AE196446917AB53CD070EBAE82D9B` | computed from the exact 270,374,484-byte artifact; matches the published `.sha256` sidecar |
| Version | 0.1.0 | app `pyproject.toml` + macOS sibling artifact's `CFBundleShortVersionString` from the same 2026-07-27 build |
| Architecture | x64 | Inno `ArchitecturesAllowed=x64` |

## BLOCKERS (fix before opening the PR)

1. **The installer URL 404s.**
   `https://animica.org/internet/animica-internet-windows-x64-setup.exe` is
   dead — the `/internet` directory was lost from the live web root in the
   2026-07-29 animica.org redeploy. The exact artifact (checksum verified)
   still exists on the server at
   `/root/site-backups/animica.org-20260729-044756/internet/`. Restore it.
   The winget validation pipeline downloads `InstallerUrl` and compares the
   hash; a 404 fails validation immediately.
2. **The URL must be immutable per version.** winget policy: once
   `Animica.AnimicaInternet 0.1.0` is merged, the bytes behind its
   `InstallerUrl` must never change. The current flat filename gets
   overwritten on each release. Re-publish as either:
   * `https://animica.org/internet/0.1.0/animica-internet-windows-x64-setup.exe`
     (versioned directory), or
   * a GitHub release asset on `animicaorg/all` (tag `anmnet-v0.1.0` already
     exists but has **no release/assets** — creating that release and
     attaching the .exe is the cleanest fix).
   Then update `InstallerUrl` in
   `Animica.AnimicaInternet.installer.yaml` before submitting.

## Human submission steps

1. Fix the two blockers above; confirm
   `curl -I <InstallerUrl>` returns 200 and
   `curl -L <InstallerUrl> | sha256sum` matches the manifest.
2. Validate locally on any Windows machine:
   ```powershell
   winget validate --manifest manifests\a\Animica\AnimicaInternet\0.1.0
   # then a real install test (requires enabling local manifests once):
   winget settings --enable LocalManifestFiles
   winget install --manifest manifests\a\Animica\AnimicaInternet\0.1.0
   ```
3. Fork https://github.com/microsoft/winget-pkgs and copy the
   `manifests/a/Animica/AnimicaInternet/0.1.0/` tree into the same path in
   the fork (the `a/` shard is the lowercased first letter of the publisher
   segment — already correct here).
4. Open a PR titled `New package: Animica.AnimicaInternet version 0.1.0`.
   The azure-pipelines bot will download the installer, hash it, run it
   silently in a sandbox and scan with Defender/SmartScreen. First-time
   publishers sometimes get a "new publisher" hold for manual review.
   * Alternative to steps 3-4: `wingetcreate submit` (or
     `wingetcreate new <InstallerUrl>` to regenerate + submit in one go) —
     it forks/PRs for you using a GitHub token you paste interactively.
     Do NOT bake a token into any script.
5. SmartScreen note: the exe is unsigned (no Authenticode certificate in the
   CI build). Unsigned installers are *allowed* in winget but the validation
   bot flags reputation issues more often; if the PR is rejected on
   SmartScreen grounds the fix is an Authenticode cert (EV or OV) in CI.
6. Future versions: run
   `wingetcreate update Animica.AnimicaInternet -u <new-versioned-url> -v <version> --submit`
   after each release — it recomputes the hash and opens the PR.
