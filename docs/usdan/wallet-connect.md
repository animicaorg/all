# Wallet Connect Flow

Frontend wallet integration lives in `apps/usdan-web/src/lib/wallet.ts`.

## Supported capabilities

- Provider detection: `window.animica`
- Connect/disconnect session flow
- Account retrieval (`animica_requestAccounts`, `animica_accounts`)
- Chain ID retrieval (`animica_chainId`, `eth_chainId` fallback)
- Message signing (`animica_signMessage`, `provider_signMessage`, `personal_sign` fallback)
- Typed payload signing (`animica_signTypedData`, `eth_signTypedData_v4` if available)
- Add token to wallet (`animica_watchAsset`, `animica_addToken`)

## Wallet test command

```bash
pnpm --filter @animica/usdan-web test
```
