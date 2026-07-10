# Chat-bridge payment wallet

The bridge pays for each AICF inference job on-chain, so it needs a wallet that
can **sign transactions on mainnet**. Configure it with:

```
ANIMICA_BRIDGE_WALLET_LABEL=<label in ~/.animica/wallets.json>
```

## The wallet MUST be `ml_dsa_65`

The network accepts **only** `ml_dsa_65` (0x1003, FIPS-204) signatures. Legacy
`sphincs_shake_128s` wallets **cannot sign on mainnet** — their only backend is
the pure-Python fallback, which is disabled by default (`ANIMICA_ALLOW_PQ_PURE_FALLBACK`),
and they are additionally stranded at the consensus layer. Pointing the bridge at
one makes every completion fail with:

```
signing failed: Pure-Python PQ fallbacks are disabled.
```

`_get_provider()` logs a loud error at startup if the configured wallet is not
`ml_dsa_65`, so the real cause is visible before the first request.

## Create + fund one

```bash
animica wallet create --label chat-bridge-mldsa --alg ml_dsa_65
# fund it — each job costs ~0.000021 ANM (21000 nANM), so a few ANM lasts a long time
```

Then set `ANIMICA_BRIDGE_WALLET_LABEL=chat-bridge-mldsa` and restart the service.
Do **not** commit `~/.animica/wallets.json` or the service env file — they hold
secret keys.
