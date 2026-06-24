# evm.animica.org — Animica EVM Bridge (dApp)

A one-page, dependency-light dApp for the **custodial EVM↔native relayer**: connect
MetaMask (chain id **149**), get an ANM deposit address (+QR), see your balance, and
send to an EVM address or withdraw to any native `anim1` wallet.

- `index.html` — the entire app (vanilla JS + `window.ethereum`; QR via cdnjs).
- Read calls (`animica_evmAccount`, `eth_getBalance`, `animica_evmBind`) go to a
  **same-origin `/rpc`** proxy to avoid CORS; the wallet itself talks to the public
  `https://mainnet.animica.org` (added via EIP-3085).

## Deploy
```bash
# 1) DNS: evm.animica.org  A  <server-ip>
# 2) doc root
sudo mkdir -p /var/www/evm.animica.org
sudo cp index.html /var/www/evm.animica.org/index.html
# 3) nginx (serves the page + proxies /rpc -> node 8545) — see nginx.evm.animica.org.conf
sudo cp nginx.evm.animica.org.conf /etc/nginx/sites-available/evm.animica.org.conf
sudo ln -sf /etc/nginx/sites-available/evm.animica.org.conf /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
# 4) TLS
sudo certbot --nginx -d evm.animica.org --redirect
```

The relayer must be enabled on the proxied node (`ANIMICA_EVM_RELAYER=1` + a KEK).
It is **custodial**: the operator holds the native keys.
