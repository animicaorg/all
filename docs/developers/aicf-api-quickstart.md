# AICF API Quickstart

## 1. Fund project in ANM

Create a project and fund its balance using ANM so calls can reserve escrow budgets.

## 2. Create API key

Generate a scoped API key from the AICF app dashboard.

## 3. Call model endpoints

```bash
curl https://aicf.animica.org/v1/chat/completions \
  -H "Authorization: Bearer $AICF_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"aicf-chat-1","messages":[{"role":"user","content":"hello"}]}'
```

## 4. Submit async jobs

Use `/v1/jobs` for batch, embedding, training, and agent workloads with explicit ANM budget caps.
