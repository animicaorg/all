import { getRpc } from './rpc'
import { normalizeTxHash } from '../utils/txHash'

export type TxLifecycleStatus = 'pending' | 'confirmed' | 'failed' | 'dropped'

export interface TxStatusResult {
  hash: string
  status: TxLifecycleStatus
  blockHeight?: number
  blockHash?: string
  confirmations?: number
}

const DEFAULT_DROP_MS = 10 * 60_000

export async function getTxStatus(
  txHash: string,
  opts: { firstSeenAt?: number; dropAfterMs?: number; explorerApiBase?: string } = {}
): Promise<TxStatusResult> {
  const hash = normalizeTxHash(txHash)
  const rpc = getRpc()

  const [head, tx, receipt] = await Promise.all([
    rpc.getHead().catch(() => null),
    rpc.getTransactionByHash(hash).catch(() => null),
    rpc.getTransactionReceipt(hash).catch(() => null)
  ])

  const blockHeight = Number((receipt as any)?.blockNumber ?? (tx as any)?.blockNumber)
  const blockHash = (receipt as any)?.blockHash ?? (tx as any)?.blockHash
  const statusRaw = String((receipt as any)?.status ?? (tx as any)?.status ?? '').toLowerCase()

  if (Number.isFinite(blockHeight) && blockHeight > 0) {
    const confirmations = head?.height ? Math.max(0, Number(head.height) - blockHeight + 1) : undefined
    const status: TxLifecycleStatus = statusRaw.includes('revert') || statusRaw === '0x0' ? 'failed' : 'confirmed'
    return { hash, status, blockHeight, blockHash, confirmations }
  }

  if (tx || (tx as any)?.pending) {
    return { hash, status: 'pending' }
  }

  if (opts.explorerApiBase) {
    try {
      const resp = await fetch(`${opts.explorerApiBase.replace(/\/$/, '')}/api/tx/${hash}`)
      if (resp.ok) {
        const data = await resp.json()
        if (data?.status === 'confirmed' || data?.status === 'failed') {
          const confs = data?.blockHeight && head?.height ? Math.max(0, Number(head.height) - Number(data.blockHeight) + 1) : undefined
          return {
            hash,
            status: data.status,
            blockHeight: data.blockHeight,
            blockHash: data.blockHash,
            confirmations: confs
          }
        }
        if (data?.status === 'pending') return { hash, status: 'pending' }
      }
    } catch {
      // ignore fallback failures
    }
  }

  const dropAfterMs = opts.dropAfterMs ?? DEFAULT_DROP_MS
  const ageMs = opts.firstSeenAt ? Date.now() - opts.firstSeenAt : 0
  if (opts.firstSeenAt && ageMs >= dropAfterMs) {
    return { hash, status: 'dropped' }
  }

  return { hash, status: 'pending' }
}
