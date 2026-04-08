# Receive QR Flow

## Payload Format

`wallet-qt` encodes the receive QR as an Animica payment URI:

```text
animica:<address>
animica:<address>?amount=<decimal-anm>&memo=<url-encoded-message>
```

Examples:

- `animica:anim1...`
- `animica:anim1...?amount=1.25`
- `animica:anim1...?amount=1.25&memo=Invoice%2042`

The QR always includes the selected receive address. Optional amount and message fields update the payload whenever they change.

## Rendering Path

The Qt receive screen does not paint a fake placeholder. Instead it:

1. builds the `animica:` URI in `ReceiveQrService`
2. calls the bundled Python helper `python -m animica.wallet_qr`
3. decodes the returned PNG into a native `QImage`
4. renders the QR in the Qt UI

The helper uses the packaged Python runtime so the same QR path works in development builds and packaged artifacts.

## Saving the QR

Use `Save QR as PNG` on the receive screen.

- output format: PNG
- content: the exact QR shown in the UI
- source payload: the current `animica:` URI for the selected account

## Failure Handling

Failure states are explicit and actionable.

- no selected address: the widget tells the user to select or create a wallet
- invalid amount: the widget requests a valid ANM amount
- missing QR dependency: the widget explains that the bundled `wallet_qt` Python extras are missing
- renderer/process failure: the widget shows the returned error detail instead of a fake QR
