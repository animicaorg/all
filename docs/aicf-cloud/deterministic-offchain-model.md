# Deterministic vs Off-Chain Execution Model

Deterministic on-chain responsibilities:

- escrow state and ANM balances
- nonce/replay protection
- deadline/challenge windows
- provider policy and mode checks
- result hash/reference acceptance state machine
- payout/refund/slash state transitions

Off-chain responsibilities:

- actual LLM/agent execution
- tool calls/runtime execution
- artifact storage and retrieval
- usage/latency metrics collection

Never done on-chain:

- raw LLM text generation
- nondeterministic external API calls
- model runtime execution inside consensus path
