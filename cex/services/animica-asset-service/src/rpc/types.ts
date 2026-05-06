/**
 * Animica RPC Types
 */

export interface RpcRequest {
  jsonrpc: "2.0";
  method: string;
  params?: any[];
  id: number | string;
}

export interface RpcResponse<T = any> {
  jsonrpc: "2.0";
  result?: T;
  error?: RpcError;
  id: number | string;
}

export interface RpcError {
  code: number;
  message: string;
  data?: any;
}

// Chain data structures
export interface BlockInfo {
  height: number;
  hash: string;
  parent_hash: string;
  timestamp: number;
  txs: string[]; // transaction IDs
}

export interface TransactionInfo {
  txid: string;
  from: string;
  to: string;
  value: string; // amount in atoms (string to preserve precision)
  nonce: number;
  gas_limit: number;
  gas_price: string;
  block_height?: number;
  block_hash?: string;
  confirmations?: number;
  status?: "pending" | "confirmed" | "failed";
}

export interface ChainHead {
  height: number;
  hash: string;
}

export interface FeeEstimate {
  gas_price: string; // in atoms
  estimated_fee: string; // total estimated fee
}

// RPC capabilities
export interface RpcCapabilities {
  supportsGetHead: boolean;
  supportsGetBlockByHeight: boolean;
  supportsGetBlockByHash: boolean;
  supportsGetTransaction: boolean;
  supportsSendRawTransaction: boolean;
  supportsWalletCreateAddress: boolean;
  supportsWalletSend: boolean;
  supportsEstimateFee: boolean;
  supportsMempoolGetPending: boolean;
  supportsMempoolGet: boolean;
  supportsStateGetAddressBalance: boolean;
  supportsStateGetBalance: boolean;
}
