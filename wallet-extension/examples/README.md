# Animica extension example page

This folder contains a tiny demo page that exercises the injected provider (`window.animica`). Use it while running a dev build or after building the extension locally.

## Load the extension in developer mode

1. Install dependencies and build or run dev:
   - `pnpm install`
   - For hot reload: `pnpm dev` (creates `dist/chrome` with live updates)
   - For a static build: `pnpm build:chrome` (outputs `dist/chrome`)
2. Open Chrome/Chromium and go to `chrome://extensions`.
3. Enable **Developer mode** (top right).
4. Click **Load unpacked** and pick the generated `dist/chrome` directory.
5. Confirm the Animica Wallet shows up in the list and that the provider injects on web pages.

## Use the example page

1. Serve this folder (or open `examples/index.html` directly). For local hosting you can run `npx serve wallet-extension/examples` or `python -m http.server` from the repo root and navigate to `http://localhost:8000/wallet-extension/examples/`.
2. With the extension loaded, open the page in a new tab.
3. Click **Request accounts** to trigger `animica_requestAccounts` and approve the connection in the extension popup.
4. Click **Get chainId** to call `animica_chainId` and verify the returned value matches the selected network in the extension.
5. Fill a recipient, value (hex), optional data, and click **Send transaction** to call `animica_sendTransaction`. Approve in the extension and watch the log for the submitted hash.

The log panel shows responses and errors to help debug connectivity while integrating dapps.
