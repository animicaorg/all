# Wallet Operator Checklist

Run this after building a development or release artifact.

## Startup

- Launch the wallet.
- Confirm the main window opens without crashing.
- Confirm wallet tabs are present: Accounts, Send, Receive, History, Address Book, Contracts, Settings.
- If built with bundled node support, confirm the Node tab opens and status/log controls render.

## Wallet Management

- Create a new wallet with `dilithium3`.
- Create a second wallet with `sphincs_shake_128s`.
- Rename one wallet.
- Mark the second wallet as default.
- Export public info.
- Export secret backup and confirm a file is written.
- Import a wallet backup into a clean test data directory.

## Balance / Receive

- Select each wallet in the receive tab.
- Confirm the correct address is shown.
- Confirm a real QR is rendered for the selected wallet.
- Enter an optional amount and message and confirm the QR refreshes.
- Use copy address and verify clipboard contents.
- Use `Save QR as PNG` and confirm the file is written and opens as a valid image.
- Confirm balance refresh updates the selected wallet.

## Send

- Open the send tab.
- Confirm recipient validation rejects malformed addresses.
- Confirm insufficient-balance validation blocks submission.
- Fill a valid transaction and review the confirmation dialog.
- Submit through a reachable node and verify a transaction hash is returned.
- Confirm the transaction appears as pending and later updates status.

## History

- Open the history tab.
- Confirm filtering by wallet, direction, status, and search works.
- Open the details dialog for a transaction.
- Export JSON and CSV.

## Address Book

- Add a contact.
- Edit the note.
- Confirm duplicate-address insertion is rejected.
- Export contacts as JSON and CSV.
- Import the exported file into a clean wallet using Merge, then Replace.

## Contracts

- Run one read call with ABI/schema mode.
- Run one raw read call.
- Preview a write payload.
- If the target network supports it, send one signed contract write and confirm the resulting transaction hash.

## Settings

- Change RPC URL, explorer URL, and polling interval.
- Save settings and restart the wallet.
- Confirm settings persist.
- Export settings, restore defaults, then import the saved file.

## Packaging

- Confirm the packaged artifact launches.
- Confirm the packaged runtime can locate the embedded node Python.
- On Linux installs, verify `/usr/lib/x86_64-linux-gnu/animica-wallet/node/venv/bin/python` or `/usr/lib/animica-wallet/node/venv/bin/python`, depending on the resolved libdir.
- On Linux AppImage/tarball, verify `usr/lib/x86_64-linux-gnu/animica-wallet/node/venv/bin/python` or `usr/lib/animica-wallet/node/venv/bin/python` after extraction.
- On macOS, verify `Contents/PlugIns/platforms/libqcocoa.dylib`.
- On Windows, verify `platforms\qwindows.dll`.
- Confirm the packaged receive tab still renders a QR and can save PNG.
