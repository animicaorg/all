# Wallet Connect

AICF web supports:

- Provider detection via `window.animica`
- `animica_requestAccounts` account connect
- Chain ID checks (`animica_chainId` / `eth_chainId`)
- Message signing (`animica_signMessage` / `personal_sign`)
- Direct contract calls (`animica_sendTransaction`)

Wallet link endpoint:

`POST /wallet/link`
