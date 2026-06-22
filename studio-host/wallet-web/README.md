# wallet.animica.org additions (deployed copies)

Tracked copies of files added to the **web wallet** (served from
`/var/www/wallet.animica.org/`) to support one-click ENA budget funding from the
Studio. These are the source of truth for those deployed assets.

- `sign/index.html` → deploy to `/var/www/wallet.animica.org/sign/index.html`.
  A dapp sign-and-send approval page (mirrors the existing `/connect/` page):
  reads a base64url sign request (`?request=…&id=…`), shows "Send N ANM to
  <to>", signs+broadcasts via the same paths as wallet.js (node `wallet.send`
  RPC, or `/wallet-sign-and-send` for browser-held keys), then POSTs
  `{requestId, approved, txid}` to the request's `callback` (the Studio).
  No change to the existing connect page, wallet.js, or the sidecar.
