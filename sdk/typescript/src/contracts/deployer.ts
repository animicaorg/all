/**
 * Contract deploy helpers.
 *
 * Responsibilities:
 *  - Build a deploy transaction from code + optional manifest bytes
 *  - Provide a high-level `deploy()` that signs, submits, and waits for receipt
 *  - Small utilities to hash artifacts and extract a contract address from receipt
 */

import type { RpcClient, WaitOpts } from '../tx/send'
import { signSendAndWait } from '../tx/send'
import type { AccessList, UnsignedTx } from '../tx/build'
import { buildDeploy, estimateIntrinsicGas } from '../tx/build'
import type { Signer } from '../wallet/signer'
import { sha3_256 } from '../utils/hash'
import { bytesToHex, hexToBytes } from '../utils/bytes'

export interface DeployParams {
  chainId: number
  from: string
  /** Contract code bytes. If string, accepts hex (0x…) or UTF-8 for dev/demo. */
  code: Uint8Array | string
  /** Optional manifest/ABI bytes packaged with the deploy. */
  manifest?: Uint8Array | string
  /** Optional value to fund contract treasury at deploy. */
  value?: bigint | number | string
  /** Sender nonce and gas price are required; gasLimit can be omitted (estimated). */
  nonce: bigint | number | string
  gasPrice: bigint | number | string
  gasLimit?: bigint | number | string
  accessList?: AccessList
}

/** Build the unsigned deploy transaction (you can sign it with your own wallet flow). */
export function buildDeployTx(p: DeployParams): UnsignedTx {
  const code = normalizeBytes(p.code)
  const manifest = p.manifest ? normalizeBytes(p.manifest) : undefined
  const combinedLen = code.length + (manifest?.length ?? 0)
  const est = estimateIntrinsicGas('deploy', combinedLen, p.accessList)
  const gasLimit = p.gasLimit ?? ((est * 130n) / 100n) // +30% headroom

  return buildDeploy({
    chainId: p.chainId,
    from: p.from,
    code,
    manifest,
    value: p.value,
    nonce: p.nonce,
    gasPrice: p.gasPrice,
    gasLimit,
    accessList: p.accessList
  })
}

export interface DeployResult<Receipt = any> {
  txHash: string
  receipt: Receipt
  /** If the chain returns it in the receipt, this will be populated. */
  contractAddress?: string
  /** Hex (0x…) hashes of the input artifacts for convenience/debugging. */
  codeHashHex: string
  manifestHashHex?: string
}

/**
 * High-level one-shot deploy:
 *  - Builds tx
 *  - Signs with the provided PQ signer
 *  - Submits to RPC
 *  - Waits for receipt
 */
export async function deploy<Receipt = any>(
  client: RpcClient,
  signer: Pick<Signer, 'getPublicKey' | 'sign' | 'alg'>,
  params: DeployParams,
  wait: WaitOpts = {}
): Promise<DeployResult<Receipt>> {
  const tx = buildDeployTx(params)
  const { txHash, receipt } = await signSendAndWait(client, tx, signer, wait)
  const contractAddress = extractContractAddress(receipt)
  const codeBytes = normalizeBytes(params.code)
  const manifestBytes = params.manifest ? normalizeBytes(params.manifest) : undefined
  const codeHashHex = '0x' + bytesToHex(sha3_256(codeBytes))
  const manifestHashHex = manifestBytes ? ('0x' + bytesToHex(sha3_256(manifestBytes))) : undefined

  return { txHash, receipt, contractAddress, codeHashHex, manifestHashHex }
}

/** Attempt to extract a contract address from the receipt (if the node includes it). */
export function extractContractAddress(receipt: any): string | undefined {
  if (receipt && typeof receipt === 'object') {
    if (typeof receipt.contractAddress === 'string') return receipt.contractAddress
    // Some nodes may include it under `address` on deploy receipts:
    if (typeof receipt.address === 'string') return receipt.address
    // Or emit a well-known event; you can extend this if your chain standardizes it.
  }
  return undefined
}

/** Utility: compute 0x-prefixed SHA3-256 of input bytes. */
export function sha3Hex(data: Uint8Array | string): string {
  const b = normalizeBytes(data)
  return '0x' + bytesToHex(sha3_256(b))
}

// ──────────────────────────────────────────────────────────────────────────────
// Internals
// ──────────────────────────────────────────────────────────────────────────────

function normalizeBytes(x: Uint8Array | string): Uint8Array {
  if (x instanceof Uint8Array) return x
  if (typeof x === 'string') {
    if (x.startsWith('0x') || x.startsWith('0X')) return hexToBytes(x)
    return new TextEncoder().encode(x)
  }
  throw new Error('Expected Uint8Array | string')
}

export default {
  buildDeployTx,
  deploy,
  extractContractAddress,
  sha3Hex
}
