import { describe, expect, it } from 'vitest'
import { bech32m } from 'bech32'
import { ExplorerService } from '../src/service'

function bech32AddressFromDigest(digestHex: string, algId = 0x1002): string {
  const digest = Buffer.from(digestHex, 'hex')
  const payload = Buffer.concat([
    Buffer.from([(algId >> 8) & 0xff, algId & 0xff]),
    digest
  ])
  return bech32m.encode('anim', bech32m.toWords(payload)).toLowerCase()
}

describe('address history population', () => {
  it('matches tx history across bech32 and hex address formats and preserves mined amount', async () => {
    const senderDigest = '11'.repeat(32)
    const recipientDigest = '22'.repeat(32)
    const senderAddress = bech32AddressFromDigest(senderDigest, 0x1002)
    const txHash = '0x' + 'a'.repeat(64)
    const blockHash = '0x' + 'b'.repeat(64)

    const tx = {
      hash: txHash,
      from: `0x${senderDigest}`,
      to: `0x${recipientDigest}`,
      payload: { v: { amount: '0x64' } }
    }
    const receipt = {
      txHash,
      blockHash,
      blockNumber: 50,
      status: 'SUCCESS'
    }

    const service = new ExplorerService({
      getHead: async () => ({ height: 50, hash: blockHash, time: 1_700_000_050 }),
      getBlockByNumber: async (height: number | string) =>
        Number(height) === 50
          ? { header: { height: 50, hash: blockHash, time: 1_700_000_050 }, txs: [tx], receipts: [receipt] }
          : { header: { height: Number(height), hash: `0x${String(height)}`, time: 1_700_000_000 }, txs: [], receipts: [] },
      getBlockByHash: async () => ({ header: { height: 50, hash: blockHash, time: 1_700_000_050 }, txs: [tx], receipts: [receipt] }),
      getTransactionByHash: async () => tx,
      getTransactionReceipt: async () => receipt,
      getMempoolPending: async () => [],
      getMempoolStats: async () => ({ count: 0, totalBytes: 0, oldestAgeSec: null }),
      getPeers: async () => [],
      getBalance: async () => '0x0'
    })

    const summary = await service.getAddressDetail(senderAddress, 10)
    expect(summary.txs).toHaveLength(1)
    expect(summary.txs[0].hash).toBe(txHash)
    expect(summary.txs[0].value).toBe('0x64')
    expect(summary.txs[0].from?.startsWith('anim1')).toBe(true)
    expect(summary.txs[0].to?.startsWith('anim1')).toBe(true)
  })
})
