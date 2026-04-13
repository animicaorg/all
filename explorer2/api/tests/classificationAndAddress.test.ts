import { createHash } from 'node:crypto'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { describe, expect, it } from 'vitest'
import { ExplorerService } from '../src/service'
import { ExplorerStore } from '../src/explorerStore'

const TX_TRANSFER = '0x' + '1'.repeat(64)
const TX_DEPLOY = '0x' + '2'.repeat(64)
const TX_CALL = '0x' + '3'.repeat(64)
const TX_CALL_FAILED = '0x' + '4'.repeat(64)
const TX_VALUE_TO_CONTRACT = '0x' + '5'.repeat(64)
const TX_CALL_UNKNOWN_ABI = '0x' + '6'.repeat(64)

const CREATOR = 'anim1creator0000000000000000000000000000000aaaa'
const EOA_TARGET = 'anim1target0000000000000000000000000000000bbbb'
const CONTRACT = 'anim1contract000000000000000000000000000000cccc'
const CONTRACT_UNVERIFIED = 'anim1contract000000000000000000000000000000eee'
const RECEIVER = 'anim1receiver000000000000000000000000000000dddd'

const ABI = [
  {
    type: 'function',
    name: 'set',
    inputs: [{ name: 'value', type: 'uint256' }],
    outputs: [],
    stateMutability: 'nonpayable'
  },
  {
    type: 'event',
    name: 'Updated',
    inputs: [{ name: 'value', type: 'uint256', indexed: false }],
    anonymous: false
  }
]

function tempDbPath(): string {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'explorer2-classify-'))
  return path.join(dir, 'explorer2-index.db')
}

function sha3Hex(input: string): string {
  return `0x${createHash('sha3-256').update(input).digest('hex')}`
}

function makeSelector(signature: string): string {
  const digest = createHash('sha3-256').update(`animica:abi:v1|${signature}`).digest()
  return `0x${digest.subarray(0, 8).toString('hex')}`
}

function encodeCallUint(selector: string, value: number): string {
  const valueHex = value.toString(16).padStart(2, '0')
  return `${selector}0101${valueHex}`.toLowerCase()
}

function makeService(includeAbi = true) {
  const blockHash = '0x' + 'a'.repeat(64)
  const selector = makeSelector('set(uint256)')
  const txInput = encodeCallUint(selector, 42)
  const eventTopic = sha3Hex('Updated(uint256)')
  const txMap: Record<string, any> = {
    [TX_TRANSFER]: { hash: TX_TRANSFER, from: CREATOR, to: RECEIVER, value: '0x10' },
    [TX_DEPLOY]: { hash: TX_DEPLOY, from: CREATOR, to: null, kind: 'contract.create', code: '0x6001600055' },
    [TX_CALL]: { hash: TX_CALL, from: CREATOR, to: CONTRACT, data: txInput, value: '0x0' },
    [TX_CALL_FAILED]: { hash: TX_CALL_FAILED, from: CREATOR, to: CONTRACT, data: txInput, value: '0x0' },
    [TX_VALUE_TO_CONTRACT]: { hash: TX_VALUE_TO_CONTRACT, from: CREATOR, to: CONTRACT, value: '0x01' },
    [TX_CALL_UNKNOWN_ABI]: { hash: TX_CALL_UNKNOWN_ABI, from: CREATOR, to: CONTRACT_UNVERIFIED, data: txInput, value: '0x0' }
  }
  const receiptMap: Record<string, any> = {
    [TX_TRANSFER]: { txHash: TX_TRANSFER, blockNumber: 100, blockHash, status: 'SUCCESS', logs: [] },
    [TX_DEPLOY]: { txHash: TX_DEPLOY, blockNumber: 100, blockHash, status: 'SUCCESS', contractAddress: CONTRACT, logs: [] },
    [TX_CALL]: { txHash: TX_CALL, blockNumber: 100, blockHash, status: 'SUCCESS', logs: [{ topics: [eventTopic], data: '0x0107' }] },
    [TX_CALL_FAILED]: { txHash: TX_CALL_FAILED, blockNumber: 100, blockHash, status: 'REVERT', logs: [] },
    [TX_VALUE_TO_CONTRACT]: { txHash: TX_VALUE_TO_CONTRACT, blockNumber: 100, blockHash, status: 'SUCCESS', logs: [] },
    [TX_CALL_UNKNOWN_ABI]: { txHash: TX_CALL_UNKNOWN_ABI, blockNumber: 100, blockHash, status: 'SUCCESS', logs: [] }
  }
  const block = {
    header: { height: 100, hash: blockHash, time: 1_700_000_100 },
    txs: [txMap[TX_TRANSFER], txMap[TX_DEPLOY], txMap[TX_CALL], txMap[TX_CALL_FAILED], txMap[TX_VALUE_TO_CONTRACT], txMap[TX_CALL_UNKNOWN_ABI]],
    receipts: [receiptMap[TX_TRANSFER], receiptMap[TX_DEPLOY], receiptMap[TX_CALL], receiptMap[TX_CALL_FAILED], receiptMap[TX_VALUE_TO_CONTRACT], receiptMap[TX_CALL_UNKNOWN_ABI]]
  }

  const store = new ExplorerStore({ dbPath: tempDbPath() })
  if (includeAbi) {
    store.upsertContractProfile({
      address: CONTRACT,
      accountType: 'contract',
      abi: ABI
    })
  }

  const service = new ExplorerService(
    {
      getHead: async () => ({ height: 100, hash: '0x' + 'f'.repeat(64), time: 1_700_000_100 }),
      getBlockByNumber: async (height: number | string) => (Number(height) === 100 ? block : { header: { height: Number(height), hash: '0x' + 'e'.repeat(64), time: 1_700_000_000 }, txs: [], receipts: [] }),
      getBlockByHash: async () => block,
      getTransactionByHash: async (hash: string) => txMap[hash] ?? null,
      getTransactionReceipt: async (hash: string) => receiptMap[hash] ?? null,
      getMempoolPending: async () => [],
      getMempoolStats: async () => ({ count: 0, totalBytes: 0, oldestAgeSec: null }),
      getPeers: async () => [],
      getBalance: async () => '0x5',
      getAccount: async (address: string) => ({ address, nonce: address === EOA_TARGET ? 2 : 1, balance: '0x5' }),
      getCode: async (address: string) => (address === CONTRACT || address === CONTRACT_UNVERIFIED ? '0x60016002556001600355' : null)
    },
    { store }
  )

  return { service, selector }
}

describe('transaction classification and address typing', () => {
  it('classifies native transfer, deployment, call, failed call, and value transfer to contract', async () => {
    const { service, selector } = makeService()

    const transfer = await service.getTxDetail(TX_TRANSFER)
    expect(transfer.classification?.type).toBe('native_transfer')

    const deployment = await service.getTxDetail(TX_DEPLOY)
    expect(deployment.classification?.type).toBe('contract_deployment')
    expect(deployment.classification?.createdContractAddress).toBe(CONTRACT)

    const call = await service.getTxDetail(TX_CALL)
    expect(call.classification?.type).toBe('contract_interaction')
    expect(call.classification?.methodSelector).toBe(selector)
    expect(call.classification?.decodedCall?.name).toBe('set')
    expect(call.classification?.decodedEvents?.[0]?.name).toBe('Updated')

    const failedCall = await service.getTxDetail(TX_CALL_FAILED)
    expect(failedCall.classification?.type).toBe('contract_interaction')
    expect(failedCall.classification?.failed).toBe(true)
    expect(failedCall.classification?.isReverted).toBe(true)

    const valueToContract = await service.getTxDetail(TX_VALUE_TO_CONTRACT)
    expect(valueToContract.classification?.type).not.toBe('contract_deployment')
  })

  it('marks EOA vs contract correctly and links creation metadata', async () => {
    const { service } = makeService()

    const contractAddress = await service.getAddressDetail(CONTRACT, 20)
    expect(contractAddress.accountType).toBe('contract')
    expect(contractAddress.contract?.creatorTxHash).toBe(TX_DEPLOY)
    expect(contractAddress.contract?.creatorAddress).toBe(CREATOR)
    expect(contractAddress.txs.some((tx) => tx.hash === TX_DEPLOY)).toBe(true)

    const eoaAddress = await service.getAddressDetail(EOA_TARGET, 20)
    expect(eoaAddress.accountType).toBe('eoa')
    expect(eoaAddress.contract).toBeNull()
  })

  it('falls back to raw input when ABI is missing', async () => {
    const { service } = makeService(false)
    const tx = await service.getTxDetail(TX_CALL_UNKNOWN_ABI)
    expect(tx.classification?.type).toBe('contract_interaction')
    expect(tx.classification?.rawInput).toMatch(/^0x[0-9a-f]+$/)
    expect(tx.classification?.decodedCall).toBeUndefined()
    expect(tx.classification?.decodedEvents ?? []).toHaveLength(0)
  })
})
