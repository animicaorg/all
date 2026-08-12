# Animica Python Cloud — working examples

Six example functions, each in its own directory with `handler.py` (the exact source that gets
deployed) and a `README.md`. They are deployed to the live platform under the designated
`examples` account and are browsable — with live stats and their on-chain anchor txids — at
`/docs/cloud/examples`.

| directory | deployed slug | demonstrates |
| --- | --- | --- |
| `hello-api/` | `hello-api` | the minimal request/response ABI; anonymous free-tier calls |
| `ai-summarizer/` | `ai-summarizer` | `animica.ai.infer` (AI_INFERENCE), honest degradation when miners aren't serving |
| `scheduled-agent/` | `scheduled-agent` | CloudSchedule + `animica.state` memory + `animica.chain.head()` |
| `paid-api/` | `anm-toolkit` | a per-call surcharge and the exact platform/developer earnings split |
| `agent-calls-app/` | `agent-calls-app` | `animica.call()` nested execution under one shared spend budget |
| `ai-python-workflow/` | `chain-pulse` | `animica.http.fetch` → pandas/numpy transform → AI summary |

## Deploy and run them

```bash
npx tsx scripts/cloud-examples.ts             # deploy (idempotent) + execute all six, print real receipts
npx tsx scripts/cloud-examples.ts --cleanup   # remove everything the script created
```

The script drives the REAL pipeline for every deploy — validation → immutable version → DA
blob → signed on-chain DEPLOY-tx anchor → ACTIVE — and then executes each function in the real
hardened sandbox, printing observed results, logs and exact metered costs. Re-runs skip
deploys whose canonical artifact hash is unchanged; editing a handler deploys the next
version.

Notes:

* The examples account holds a Founding-Developer Pro grant (fee benefit expired, so its
  sales settle at the standard platform fee) — the in-model way to keep six public functions
  published without a PayPal subscription.
* The demo caller account is funded through the platform ledger and pays real nANM for the
  paid examples; the `examples` account's earnings are real, spendable balance.
* The two AI examples call `animica.ai.infer` and fall back to real deterministic computation
  (extractive summarization / composed stats report) when the miner network is not serving
  inference, reporting which engine produced the result. Re-run the script while miners are
  serving to observe the `animica-ai` path.
