export type GoldenConversation = {
  name: string;
  modelOutput: string;
  expectMethods: string[];
};

export const goldenConversations: GoldenConversation[] = [
  {
    name: "deploy simple transfer tx",
    modelOutput: `contract TransferHelper {
  fn build_transfer() -> Tx {
    return Tx {
      kind: "transfer",
      data: { to: "anim1qq...", amount: "1" },
      gasLimit: "21000",
      fee: "1000",
      nonce: "<state.getNextNonce>",
      chainId: "<chain.getParams.chainId>"
    };
  }
}

Minimal Example
- Use tx.decodeRawTransaction before submit.

Deployment Recipe
1. Call state.getNextNonce.
2. Call chain.getParams.
3. Submit with tx.sendRawTransaction.`,
    expectMethods: ["state.getNextNonce", "chain.getParams", "tx.sendRawTransaction", "tx.decodeRawTransaction"]
  },
  {
    name: "deploy minimal contract and call it",
    modelOutput: `contract Counter {
  state count: u64;

  fn increment() {
    count = count + 1;
  }
}
We can call it after deploy.`,
    expectMethods: ["tx.sendRawTransaction", "state.getNextNonce", "chain.getParams"]
  },
  {
    name: "tx rejected then explain and fix fee/nonce/chainId",
    modelOutput: `contract RetryFlow {
  fn deploy() {}
}
If tx is rejected, call tx.explainReject.
Then use tx.magicRecover and resubmit.
If still rejected, run tx.debugVerifyRawTransaction with the rawTx and fix fee, nonce, chainId.`,
    expectMethods: ["tx.explainReject", "tx.debugVerifyRawTransaction"]
  }
];
