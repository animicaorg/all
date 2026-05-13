# Animica Runtime Release Channel

This runbook is for release engineers producing artifacts consumed by:

```sh
npm install -g animica-node animica-agent
animica-node install-runtime
```

The canonical stable channel layout is:

```text
https://releases.animica.org/runtime/stable/manifest.json
https://releases.animica.org/runtime/stable/animica-runtime-stable-0.1.0-linux-x64.tar.gz
https://releases.animica.org/runtime/stable/animica-runtime-stable-0.1.0-linux-arm64.tar.gz
https://releases.animica.org/runtime/stable/animica-runtime-stable-0.1.0-linux-armv7.tar.gz
https://releases.animica.org/runtime/stable/animica-runtime-stable-0.1.0-win32-x64.tar.gz
https://releases.animica.org/runtime/stable/animica-runtime-stable-0.1.0-win32-arm64.tar.gz
```

`beta` and `dev` use the same layout with the channel path and tarball name changed.

## Build Inputs

A production bundle should include:

- `../../python` copied as the Animica Python source root.
- A target-matching relocatable Python tree passed with `--python`.

Without `--python`, the bundle is structurally valid but requires `python3` or
`python` on the end-user machine. Do not call that a zero-dependency runtime.

## Build Commands

Run from `packages/animica-node`.

Native Linux x64:

```sh
node scripts/build-runtime-bundle.mjs \
  --channel stable \
  --version 0.1.0 \
  --platform linux \
  --arch x64 \
  --src ../../python \
  --python /absolute/path/to/linux-x64/relocatable-python \
  --output dist/runtime
```

Native Linux arm64:

```sh
node scripts/build-runtime-bundle.mjs \
  --channel stable \
  --version 0.1.0 \
  --platform linux \
  --arch arm64 \
  --src ../../python \
  --python /absolute/path/to/linux-arm64/relocatable-python \
  --output dist/runtime
```

Linux armv7 is only supported when you have a working armv7 Python/runtime tree:

```sh
node scripts/build-runtime-bundle.mjs \
  --channel stable \
  --version 0.1.0 \
  --platform linux \
  --arch armv7 \
  --src ../../python \
  --python /absolute/path/to/linux-armv7/relocatable-python \
  --output dist/runtime
```

Native Windows x64, from PowerShell or Git Bash in `packages/animica-node`:

```sh
node scripts/build-runtime-bundle.mjs \
  --channel stable \
  --version 0.1.0 \
  --platform win32 \
  --arch x64 \
  --src ../../python \
  --python C:/absolute/path/to/win32-x64/python \
  --output dist/runtime
```

Windows arm64 requires a Windows arm64 Python/runtime tree:

```sh
node scripts/build-runtime-bundle.mjs \
  --channel stable \
  --version 0.1.0 \
  --platform win32 \
  --arch arm64 \
  --src ../../python \
  --python C:/absolute/path/to/win32-arm64/python \
  --output dist/runtime
```

## Manifest And Validation

After copying all real tarballs into one directory:

```sh
node scripts/generate-runtime-manifest.mjs \
  --input dist/runtime \
  --channel stable \
  --version 0.1.0 \
  --base https://releases.animica.org/runtime/stable \
  --output dist/runtime/manifest.json \
  --force

node scripts/validate-runtime-release.mjs \
  --input dist/runtime \
  --manifest dist/runtime/manifest.json \
  --require-platforms linux-x64,win32-x64
```

Host-platform install smoke:

```sh
pnpm --filter "@animica/agent-core" --filter "animica-node" install --no-frozen-lockfile
pnpm --filter "@animica/agent-core" build
pnpm --filter "animica-node" build
node scripts/smoke-runtime-manifest.mjs --manifest-url "$(pwd)/dist/runtime/manifest.json"
```

Structural validation for a non-host target:

```sh
node scripts/smoke-runtime-manifest.mjs \
  --manifest-url "$(pwd)/dist/runtime/manifest.json" \
  --platform win32-x64
```

This validates manifest parsing, download/local file fetch, sha256, extraction,
and the entry file. It does not prove native Windows execution.

## Upload

Print exact commands:

```sh
node scripts/print-runtime-upload-commands.mjs \
  --dir dist/runtime \
  --channel stable \
  --host user@releases.animica.org \
  --remote-root /var/www/releases.animica.org/runtime
```

The default rsync shape is:

```sh
ssh user@releases.animica.org 'mkdir -p /var/www/releases.animica.org/runtime/stable'
rsync -avz --checksum dist/runtime/ user@releases.animica.org:/var/www/releases.animica.org/runtime/stable/
```

Nginx example:

```nginx
server {
    server_name releases.animica.org;

    root /var/www/releases.animica.org;
    autoindex off;

    location / {
        try_files $uri =404;
    }
}
```

Verify after upload:

```sh
curl -fsSI https://releases.animica.org/runtime/stable/manifest.json
curl -fsS https://releases.animica.org/runtime/stable/manifest.json
ANIMICA_RUNTIME_HOME="$(mktemp -d)" animica-node runtime doctor
ANIMICA_RUNTIME_HOME="$(mktemp -d)" animica-node install-runtime
```

## Signatures

`generate-runtime-manifest.mjs` can add an Ed25519 signature:

```sh
node scripts/generate-runtime-manifest.mjs \
  --input dist/runtime \
  --channel stable \
  --version 0.1.0 \
  --base https://releases.animica.org/runtime/stable \
  --output dist/runtime/manifest.json \
  --sign-key /secure/path/runtime-manifest-ed25519-private.pem \
  --key-id runtime-2026-05
```

Clients verify signed manifests when `ANIMICA_RUNTIME_MANIFEST_PUBLIC_KEY` is
set. Unsigned manifests are currently accepted for backwards compatibility.

## Troubleshooting

- HTTP 404 on `manifest.json`: the channel directory or manifest was not uploaded.
- HTTP 404 on a tarball: manifest URLs do not match uploaded filenames.
- Checksum mismatch: regenerate the manifest from the exact files in the upload directory.
- No platform match: publish a tarball named with this machine's platform key, such as `linux-x64` or `win32-x64`.
- TLS/certificate errors: fix the release host certificate chain before retrying.
