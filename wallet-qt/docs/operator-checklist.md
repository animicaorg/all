# Release Checklist

## Product checks

- The app launches without starting a local node.
- Settings show Animica mainnet and `https://rpc.animica.org`.
- There is no Node tab or local-node control surface.
- Send, receive, history, and account screens initialize normally.

## Packaging checks

- No artifact contains `node/venv`.
- No artifact contains bundled genesis/spec assets.
- Install/layout verification scripts pass.

## Smoke checks

- Launch the app.
- Confirm the remote connection indicator behaves sensibly.
- Disconnect network access or point the environment at a blocked path only for testing and confirm the banner/error state is clear.

## Documentation checks

- README describes the remote-only model.
- build/packaging/release docs do not mention embedded-node flows.
- release notes explain that node operation belongs to other Animica tooling.
