/**
 * Generic ABI-based contract client.
 *
 * Responsibilities:
 *  - Validate and hold a contract address + ABI
 *  - Encode call data from ABI (function name + args)
 *  - Build call transactions (UnsignedTx) ready to sign/send
 *  - Send a call (sign → submit → await receipt) via a provided Signer
 *  - Decode return values (for read calls that return data in logs/receipts)
 *
 * This client intentionally does NOT perform on-chain simulation; use your node's
 * simulate APIs (if exposed) or the studio-wasm tool for in-browser simulation.
 */

import { assertAddress } from '../address'
import type { ABI } from '../types/abi'
import { normalizeABI, encodeCallData, decodeReturnData } from '../types/abi'
import type { AccessList, UnsignedTx } from '../tx/build'
import { buildCall, estimateIntrinsicGas } from '../tx/build'
import type { RpcClient, WaitOpts } from '../tx/send'
import { signSendAndWait } from '../tx/send'
import type { Signer } from '../wallet/signer'

export interface CallTxParams {
  chainId: number
  from: string
  nonce: bigint | number | string
  gasPrice: bigint | number | string
  /** Optional override; if omitted we compute a conservative estimate. */
  gasLimit?: bigint | number | string
  /** Optional value to send along with the call. */
  value?: bigint | number | string
  accessList?: AccessList
}

export class Contract {
  readonly address: string
  readonly abi: ABI
  private readonly _abi = normalizeABI

  constructor(address: string, abi: ABI) {
    assertAddress(address)
    this.address = address
    this.abi = abi
    // quick normalization check will throw early if ABI is malformed
    normalizeABI(abi)
  }

  /** Encode a function call's data bytes using the ABI. */
  encode(method: string, args: unknown[] = []): Uint8Array {
    return encodeCallData(this.abi, method, args)
  }

  /** Decode a function's return data bytes using the ABI. */
  decode(method: string, returnData: Uint8Array | string): unknown {
    return decodeReturnData(this.abi, method, returnData)
  }

  /**
   * Build an UnsignedTx for a contract call.
   * You can pass this to your wallet flow or call `send()` below with a Signer.
   */
  buildCallTx(method: string, args: unknown[], p: CallTxParams): UnsignedTx {
    const data = this.encode(method, args)
    const accessList = p.accessList
    const est = estimateIntrinsicGas('call', data.length, accessList)
    const gasLimit = p.gasLimit ?? ((est * 120n) / 100n) // +20% headroom by default

    return buildCall({
      chainId: p.chainId,
      from: p.from,
      to: this.address,
      data,
      value: p.value,
      nonce: p.nonce,
      gasPrice: p.gasPrice,
      gasLimit,
      accessList
    })
  }

  /**
   * High-level helper: build → sign → send → wait for receipt.
   * Returns { txHash, receipt }.
   */
  async send(
    client: RpcClient,
    signer: Pick<Signer, 'getPublicKey' | 'sign' | 'alg'>,
    method: string,
    args: unknown[],
    p: CallTxParams,
    wait: WaitOpts = {}
  ) {
    const tx = this.buildCallTx(method, args, p)
    return signSendAndWait(client, tx, signer, wait)
  }
}

/** Convenience factory. */
export function at(address: string, abi: ABI): Contract {
  return new Contract(address, abi)
}

export default { Contract, at }
