import request from 'supertest'
import { describe, expect, it } from 'vitest'
import { ExplorerService } from '../src/service'
import { createServer } from '../src/server'

const TX_CREATE = '0x' + '1'.repeat(64)
const TX_PACKAGE = '0x' + '2'.repeat(64)
const TX_FAILED = '0x' + '3'.repeat(64)
const TX_TRANSFER = '0x' + '4'.repeat(64)
const TX_NO_RECEIPT = '0x' + '5'.repeat(64)
const BLOCK_20 = '0x' + 'a'.repeat(64)
const BLOCK_19 = '0x' + 'b'.repeat(64)
const BLOCK_30 = '0x' + 'c'.repeat(64)

function makeService(): ExplorerService {
  return new ExplorerService({
    getHead: async () => ({ height: 20, hash: '0x' + 'f'.repeat(64), time: 1_700_000_020 }),
    getBlockByNumber: async (height: number | string) => {
      const n = Number(height)
      if (n === 20) {
        return {
          header: { height: 20, hash: BLOCK_20, time: 1_700_000_020 },
          txs: [
            {
              hash: TX_CREATE,
              from: 'anim1creator000000000000000000000000000000x4x7h',
              kind: 'contract.create',
              data: '0x6001600055'
            },
            {
              hash: TX_PACKAGE,
              from: 'anim1publisher000000000000000000000000000xsx5w',
              to: 'system:vm',
              method: 'package.publish',
              packageBytes: '0x00ff11aa'
            },
            {
              hash: TX_TRANSFER,
              from: 'anim1from0000000000000000000000000000000k9fk9',
              to: 'anim1to00000000000000000000000000000000x8jxf',
              value: '0x1'
            }
          ],
          receipts: [
            { txHash: TX_CREATE, status: 'SUCCESS', gasUsed: '0x5208', blockNumber: 20, blockHash: BLOCK_20, contractAddress: 'anim1contract0000000000000000000000000000a8rqf' },
            { txHash: TX_PACKAGE, status: 'SUCCESS', gasUsed: '0x4100', blockNumber: 20, blockHash: BLOCK_20 },
            { txHash: TX_TRANSFER, status: 'SUCCESS', gasUsed: '0x2100', blockNumber: 20, blockHash: BLOCK_20 }
          ]
        }
      }
      if (n === 19) {
        return {
          header: { height: 19, hash: BLOCK_19, time: 1_700_000_019 },
          txs: [
            {
              hash: TX_FAILED,
              from: 'anim1failed00000000000000000000000000000sgq2g',
              to: '0x0',
              action: 'deploy_contract'
            }
          ],
          receipts: [
            { txHash: TX_FAILED, status: 'REVERT', gasUsed: '0x3200', blockNumber: 19, blockHash: BLOCK_19 }
          ]
        }
      }
      return { header: { height: n, hash: '0x' + 'c'.repeat(64), time: 1_700_000_000 + n }, txs: [], receipts: [] }
    },
    getBlockByHash: async () => null,
    getTransactionByHash: async () => null,
    getTransactionReceipt: async () => null,
    getMempoolPending: async () => [],
    getMempoolStats: async () => ({ count: 0, totalBytes: 0, oldestAgeSec: null }),
    getPeers: async () => [],
    getBalance: async () => '0x0'
  })
}

describe('contract deployment feed', () => {
  it('extracts create/package deployments and excludes normal transfers', async () => {
    const service = makeService()
    const feed = await service.getContractDeployments(10, 2)

    expect(feed.headHeight).toBe(20)
    expect(feed.items).toHaveLength(3)
    expect(feed.stats.total).toBe(3)
    expect(feed.stats.successful).toBe(2)
    expect(feed.stats.failed).toBe(1)

    const create = feed.items.find((item) => item.txHash === TX_CREATE)
    expect(create?.kind).toBe('contract_create')
    expect(create?.contractAddress).toBe('anim1contract0000000000000000000000000000a8rqf')

    const pkg = feed.items.find((item) => item.txHash === TX_PACKAGE)
    expect(pkg?.kind).toBe('package_publish')

    const failed = feed.items.find((item) => item.txHash === TX_FAILED)
    expect(failed?.status).toBe('failed')
  })

  it('serves /api/contracts/deployments', async () => {
    const service = makeService()
    const app = createServer(service, '*', 'silent')

    const res = await request(app).get('/api/contracts/deployments?limit=2&scanBlocks=2')
    expect(res.status).toBe(200)
    expect(res.body.items).toHaveLength(2)
    expect(res.body.items[0].txHash).toBeDefined()
    expect(res.body.stats.total).toBe(2)
  })

  it('treats block-included deploy txs as confirmed even when receipts are temporarily missing', async () => {
    const service = new ExplorerService({
      getHead: async () => ({ height: 30, hash: '0x' + 'f'.repeat(64), time: 1_700_000_030 }),
      getBlockByNumber: async (height: number | string) => {
        const n = Number(height)
        if (n === 30) {
          return {
            header: { height: 30, hash: BLOCK_30, time: 1_700_000_030 },
            txs: [
              {
                hash: TX_NO_RECEIPT,
                from: 'anim1creator000000000000000000000000000000x4x7h',
                kind: 'contract.create'
              }
            ],
            receipts: []
          }
        }
        return { header: { height: n, hash: '0x' + 'd'.repeat(64), time: 1_700_000_000 + n }, txs: [], receipts: [] }
      },
      getBlockByHash: async () => null,
      getTransactionByHash: async () => null,
      getTransactionReceipt: async () => null,
      getMempoolPending: async () => [],
      getMempoolStats: async () => ({ count: 0, totalBytes: 0, oldestAgeSec: null }),
      getPeers: async () => [],
      getBalance: async () => '0x0'
    })

    const feed = await service.getContractDeployments(10, 1)
    expect(feed.items).toHaveLength(1)
    expect(feed.items[0].txHash).toBe(TX_NO_RECEIPT)
    expect(feed.items[0].status).toBe('confirmed')
    expect(feed.items[0].kind).toBe('contract_create')
  })
})
