/**
 * Contract Deployment Logic
 */

import { sendTransaction } from "../wallet/adapter";
import { RPCClient } from "../rpc/client";

export interface DeployParams {
  chainId: number;
  from: string;
  code: Uint8Array;
  manifest: any;
  value?: string;
  gasPrice?: string;
  gasLimit?: number;
}

export interface DeployResult {
  txHash: string;
  receipt?: any;
  contractAddress?: string;
  codeHashHex?: string;
  manifestHashHex?: string;
}

/**
 * Build an unsigned deploy transaction
 */
export function buildDeployTx(params: DeployParams): any {
  return {
    chainId: params.chainId,
    from: params.from,
    to: null, // Deploy transaction has no 'to' address
    value: params.value || "0",
    data: {
      code: Array.from(params.code),
      manifest: params.manifest,
    },
    gasPrice: params.gasPrice || "1",
    gasLimit: params.gasLimit || 1000000,
  };
}

/**
 * Deploy a contract
 */
export async function deployContract(
  client: RPCClient,
  params: DeployParams
): Promise<DeployResult> {
  // Build transaction
  const tx = buildDeployTx(params);

  // Sign and send via wallet
  const txHash = await sendTransaction(tx);

  // Poll for receipt
  let receipt = null;
  for (let i = 0; i < 60; i++) {
    await new Promise((resolve) => setTimeout(resolve, 1000));
    try {
      receipt = await client.getReceipt(txHash);
      if (receipt) {
        break;
      }
    } catch (error) {
      // Receipt not ready yet
    }
  }

  return {
    txHash,
    receipt,
    contractAddress: receipt?.contractAddress,
    codeHashHex: receipt?.codeHash,
    manifestHashHex: receipt?.manifestHash,
  };
}

/**
 * Estimate gas for deployment
 */
export async function estimateDeployGas(
  client: RPCClient,
  params: DeployParams
): Promise<number> {
  const tx = buildDeployTx(params);
  return client.estimateGas(tx);
}
