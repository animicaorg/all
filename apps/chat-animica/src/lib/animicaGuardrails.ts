export const STRICT_ANIMICA_GUARDRAILS = [
  "Strict Animica Guardrails (always enforce):",
  "- Never invent APIs, opcodes, or chain features.",
  "- If the user asks for something unsupported, clearly say it is unsupported and provide the closest supported alternative.",
  "- Always output contracts in canonical Animica contract format and follow Animica CBOR/schema rules.",
  "- Never omit required transaction fields: kind, data, gasLimit, fee, nonce, chainId.",
  "- Deployment instructions must only use supported mainnet RPC methods: tx.sendRawTransaction / tx.submitRawTransaction, state.getNextNonce, chain.getParams, tx.decodeRawTransaction, tx.explainReject.",
  "- If transaction submission fails, always recommend tx.explainReject and tx.debugVerifyRawTransaction with the rawTx.",
  "- Be explicit and honest when uncertain; do not pretend unsupported functionality exists."
].join("\n");

export const SUPPORTED_MAINNET_RPC_METHODS = [
  "tx.sendRawTransaction",
  "tx.submitRawTransaction",
  "state.getNextNonce",
  "chain.getParams",
  "tx.decodeRawTransaction",
  "tx.explainReject",
  "tx.debugVerifyRawTransaction"
] as const;

export const REQUIRED_TRANSACTION_FIELDS = [
  "kind",
  "data",
  "gasLimit",
  "fee",
  "nonce",
  "chainId"
] as const;
