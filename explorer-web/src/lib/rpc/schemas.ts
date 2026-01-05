/**
 * Zod schemas for validating RPC responses
 * Ensures type safety and catches unexpected response shapes early
 */

import { z } from 'zod';

// ============================================================================
// Common/Primitive Schemas
// ============================================================================

export const hexStringSchema = z.string().regex(/^0x[0-9a-fA-F]*$/);
export const addressSchema = z.string().min(1);
export const hashSchema = z.string().min(1);
export const isoDateSchema = z.string().datetime().or(z.string());

// ============================================================================
// Chain Status & Head
// ============================================================================

export const chainHeadSchema = z.object({
  height: z.number().int().nonnegative(),
  hash: hashSchema,
  timeISO: isoDateSchema,
  timestamp: z.number().optional(),
  timestamp_ms: z.number().optional(),
  number: z.number().optional(), // alias for height
  blockHash: hashSchema.optional(), // alias for hash
});

export type ChainHead = z.infer<typeof chainHeadSchema>;

export const syncPhaseSchema = z.enum([
  'idle',
  'headers',
  'syncing',
  'fully-synced',
  'catching-up',
  'backfilling',
]);

export const chainStatusSchema = z.object({
  chainId: z.union([z.string(), z.number()]),
  networkName: z.string().optional(),
  head: chainHeadSchema.optional(),
  syncPhase: syncPhaseSchema.optional(),
  syncProgress: z.number().min(0).max(1).optional(), // 0.0 to 1.0
  difficulty: z.union([z.string(), z.number()]).optional(),
  target: z.union([z.string(), z.number()]).optional(),
  nodeVersion: z.string().optional(),
  peers: z.number().int().nonnegative().optional(),
});

export type ChainStatus = z.infer<typeof chainStatusSchema>;

// ============================================================================
// Block Schema
// ============================================================================

export const blockHeaderSchema = z.object({
  height: z.number().int().nonnegative(),
  hash: hashSchema,
  parentHash: hashSchema.optional(),
  timeISO: isoDateSchema,
  timestamp: z.number().optional(),
  timestamp_ms: z.number().optional(),
  proposer: addressSchema.optional(),
  miner: addressSchema.optional(), // alias for proposer
  reward: z.union([z.string(), z.number()]).optional(),
  difficulty: z.union([z.string(), z.number()]).optional(),
  gasUsed: z.union([z.string(), z.number()]).optional(),
  gasLimit: z.union([z.string(), z.number()]).optional(),
  size: z.number().optional(),
  weight: z.number().optional(),
  stateRoot: hashSchema.optional(),
  receiptsRoot: hashSchema.optional(),
  daRoot: hashSchema.optional(),
  number: z.number().optional(), // alias for height
  blockHash: hashSchema.optional(), // alias for hash
});

export type BlockHeader = z.infer<typeof blockHeaderSchema>;

export const txSummarySchema = z.object({
  hash: hashSchema,
  from: addressSchema,
  to: addressSchema.nullable().optional(),
  value: z.union([z.string(), z.number()]),
  nonce: z.number().int().nonnegative(),
  blockHeight: z.number().int().nonnegative().optional(),
  blockHash: hashSchema.optional(),
  index: z.number().int().nonnegative().optional(),
  status: z.enum(['pending', 'executed', 'failed', 'success']).optional(),
});

export type TxSummary = z.infer<typeof txSummarySchema>;

export const blockDetailSchema = blockHeaderSchema.extend({
  txCount: z.number().int().nonnegative().optional(),
  txs: z.array(txSummarySchema).optional(),
  transactions: z.array(txSummarySchema).optional(), // alias
  confirmations: z.number().int().nonnegative().optional(),
  // PoIES-specific fields (optional)
  poies: z.object({
    gamma: z.number().optional(),
    fairness: z.number().optional(),
    mix: z.number().optional(),
  }).optional(),
});

export type BlockDetail = z.infer<typeof blockDetailSchema>;

// ============================================================================
// Transaction Schema
// ============================================================================

export const logEntrySchema = z.object({
  address: addressSchema,
  topics: z.array(hashSchema),
  data: z.string(),
  logIndex: z.number().optional(),
  transactionIndex: z.number().optional(),
  blockNumber: z.number().optional(),
  blockHash: hashSchema.optional(),
});

export type LogEntry = z.infer<typeof logEntrySchema>;

export const receiptSchema = z.object({
  transactionHash: hashSchema,
  transactionIndex: z.number().int().nonnegative().optional(),
  blockHash: hashSchema.optional(),
  blockNumber: z.number().int().nonnegative().optional(),
  from: addressSchema,
  to: addressSchema.nullable().optional(),
  cumulativeGasUsed: z.union([z.string(), z.number()]).optional(),
  gasUsed: z.union([z.string(), z.number()]),
  contractAddress: addressSchema.nullable().optional(),
  logs: z.array(logEntrySchema).optional(),
  logsBloom: z.string().optional(),
  status: z.union([z.boolean(), z.number(), z.string()]),
  returnData: z.string().optional(),
});

export type Receipt = z.infer<typeof receiptSchema>;

export const signatureMetadataSchema = z.object({
  algorithm: z.string().optional(),
  publicKey: z.string().optional(),
  signature: z.string().optional(),
  r: z.string().optional(),
  s: z.string().optional(),
  v: z.union([z.string(), z.number()]).optional(),
});

export type SignatureMetadata = z.infer<typeof signatureMetadataSchema>;

export const txDetailSchema = z.object({
  hash: hashSchema,
  from: addressSchema,
  to: addressSchema.nullable().optional(),
  value: z.union([z.string(), z.number()]),
  nonce: z.number().int().nonnegative(),
  blockHeight: z.number().int().nonnegative().optional(),
  blockNumber: z.number().int().nonnegative().optional(), // alias
  blockHash: hashSchema.optional(),
  transactionIndex: z.number().int().nonnegative().optional(),
  index: z.number().int().nonnegative().optional(), // alias
  status: z.enum(['pending', 'executed', 'failed', 'success']).optional(),
  
  // Gas & fees
  gas: z.union([z.string(), z.number()]).optional(),
  gasLimit: z.union([z.string(), z.number()]).optional(),
  gasPrice: z.union([z.string(), z.number()]).optional(),
  maxFeePerGas: z.union([z.string(), z.number()]).optional(),
  maxPriorityFeePerGas: z.union([z.string(), z.number()]).optional(),
  fee: z.union([z.string(), z.number()]).optional(),
  
  // Data
  input: z.string().optional(),
  data: z.string().optional(), // alias for input
  
  // Receipt
  receipt: receiptSchema.optional(),
  
  // Signature
  signatureMetadata: signatureMetadataSchema.optional(),
  r: z.string().optional(),
  s: z.string().optional(),
  v: z.union([z.string(), z.number()]).optional(),
  
  // Timestamps
  timestamp: z.number().optional(),
  timestamp_ms: z.number().optional(),
  timeISO: isoDateSchema.optional(),
});

export type TxDetail = z.infer<typeof txDetailSchema>;

// ============================================================================
// Address/Account Schema
// ============================================================================

export const addressDetailSchema = z.object({
  address: addressSchema,
  balance: z.union([z.string(), z.number()]),
  balancePending: z.union([z.string(), z.number()]).optional(),
  nonce: z.number().int().nonnegative(),
  codeHash: hashSchema.nullable().optional(),
  isContract: z.boolean().optional(),
  txCount: z.number().int().nonnegative().optional(),
  // Token balances (if supported)
  tokens: z.array(z.object({
    token: addressSchema,
    balance: z.union([z.string(), z.number()]),
    symbol: z.string().optional(),
    decimals: z.number().optional(),
  })).optional(),
});

export type AddressDetail = z.infer<typeof addressDetailSchema>;

// ============================================================================
// Mempool Schema
// ============================================================================

export const mempoolEntrySchema = z.object({
  hash: hashSchema,
  from: addressSchema,
  to: addressSchema.nullable().optional(),
  value: z.union([z.string(), z.number()]),
  nonce: z.number().int().nonnegative(),
  gas: z.union([z.string(), z.number()]).optional(),
  gasPrice: z.union([z.string(), z.number()]).optional(),
  fee: z.union([z.string(), z.number()]).optional(),
  timestamp: z.number().optional(),
  rejectionReason: z.string().optional(),
});

export type MempoolEntry = z.infer<typeof mempoolEntrySchema>;

export const mempoolStatusSchema = z.object({
  size: z.number().int().nonnegative(),
  txs: z.array(mempoolEntrySchema).optional(),
  bytes: z.number().optional(),
});

export type MempoolStatus = z.infer<typeof mempoolStatusSchema>;

// ============================================================================
// Peers Schema
// ============================================================================

export const peerInfoSchema = z.object({
  id: z.string(),
  address: z.string().optional(),
  inbound: z.boolean(),
  version: z.string().optional(),
  latency: z.number().optional(),
  height: z.number().optional(),
});

export type PeerInfo = z.infer<typeof peerInfoSchema>;

export const peersStatusSchema = z.object({
  total: z.number().int().nonnegative(),
  inbound: z.number().int().nonnegative(),
  outbound: z.number().int().nonnegative(),
  peers: z.array(peerInfoSchema).optional(),
});

export type PeersStatus = z.infer<typeof peersStatusSchema>;

// ============================================================================
// Fee Policy Schema
// ============================================================================

export const feePolicySchema = z.object({
  baseFee: z.union([z.string(), z.number()]).optional(),
  minGasPrice: z.union([z.string(), z.number()]).optional(),
  maxGasPrice: z.union([z.string(), z.number()]).optional(),
  gasCeiling: z.union([z.string(), z.number()]).optional(),
});

export type FeePolicy = z.infer<typeof feePolicySchema>;

// ============================================================================
// Helper: Safe parse with detailed error logging
// ============================================================================

export function safeParse<T>(
  schema: z.ZodSchema<T>,
  data: unknown,
  context?: string
): { success: true; data: T } | { success: false; error: string } {
  const result = schema.safeParse(data);
  if (result.success) {
    return { success: true, data: result.data };
  }
  
  const errorMsg = `Schema validation failed${context ? ` for ${context}` : ''}: ${result.error.message}`;
  console.error(errorMsg, { data, errors: result.error.errors });
  
  return { success: false, error: errorMsg };
}
