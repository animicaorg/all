/**
 * BitGo Types and Interfaces
 */

/**
 * Normalized deposit observation from BitGo webhook
 */
export interface DepositObservation {
  provider: "BITGO";
  providerEventId: string; // unique per webhook delivery or transfer id
  walletId: string; // BitGo wallet id
  coin: string; // e.g. btc, eth, usdt, usdc
  networkCode: string; // map coin->network (BTC/ETH/...)
  assetSymbol: string; // map coin/token->asset
  txid: string;
  voutOrLogIndex?: string; // UTXO vout or EVM log index if available
  address: string; // destination address
  tag?: string; // memo/tag if relevant
  amountAtoms: bigint; // normalize to asset decimals atoms
  confirmations: number;
  blockHeight?: number;
  blockHash?: string;
  observedAt: Date;
  status: "DETECTED" | "CONFIRMED" | "FAILED";
  transferId?: string; // BitGo transfer id if present
  raw: any;
}

/**
 * BitGo webhook payload (simplified)
 * Based on BitGo's actual webhook structure
 */
export interface BitGoWebhookPayload {
  type: string; // "transfer", "transaction", etc.
  walletId: string;
  coin: string;
  hash?: string; // transaction hash
  transfer?: {
    id: string;
    coin: string;
    wallet: string;
    txid: string;
    height?: number;
    heightId?: string;
    date: string;
    confirmations: number;
    value: number | string;
    valueString: string;
    outputs?: Array<{
      address: string;
      value: number | string;
      valueString: string;
      wallet?: string;
    }>;
    entries?: Array<{
      address: string;
      value: number | string;
      valueString: string;
      wallet?: string;
    }>;
    state: string; // "confirmed", "unconfirmed", "signed", "failed"
  };
  // ERC20/EVM specific
  tokenContractAddress?: string;
}

/**
 * BitGo transfer state mapping
 */
export const BitGoTransferState = {
  CONFIRMED: "confirmed",
  UNCONFIRMED: "unconfirmed",
  SIGNED: "signed",
  FAILED: "failed",
  REMOVED: "removed",
} as const;

/**
 * Deposit status
 */
export type DepositStatus = 
  | "DETECTED"   // Initial detection
  | "CONFIRMED"  // Meets confirmation requirements
  | "CREDITED"   // Balance credited
  | "FAILED"     // Transaction failed
  | "REORGED"    // Chain reorganization
  | "HOLD";      // Manual or risk hold

/**
 * Risk check result
 */
export interface RiskCheckResult {
  ok: boolean;
  hold: boolean;
  reason?: string;
  flags: string[];
}
