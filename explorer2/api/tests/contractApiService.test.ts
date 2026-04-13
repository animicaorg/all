import { createHash } from 'node:crypto'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { describe, expect, it } from 'vitest'
import { ContractVerifier } from '../src/contractVerifier'
import { ExplorerStore } from '../src/explorerStore'
import { ExplorerService } from '../src/service'

const ADDRESS = 'anim1routecontract00000000000000000000000000xyz'
const CREATOR_TX = '0x' + '9'.repeat(64)
const RUNTIME = '0x6001600255'
const CREATION = '0x60056001600255'

function tempDbPath(): string {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'explorer2-contract-api-'))
  return path.join(dir, 'explorer2-index.db')
}

function hashHex(value: string): string {
  const normalized = value.startsWith('0x') ? value.slice(2) : value
  return `0x${createHash('sha3-256').update(Buffer.from(normalized, 'hex')).digest('hex')}`
}

async function waitForJob(service: ExplorerService, jobId: string) {
  for (let i = 0; i < 120; i += 1) {
    const job = service.getContractVerificationJob(jobId)
    if (job && (job.status === 'verified' || job.status === 'failed')) return job
    await new Promise((resolve) => setTimeout(resolve, 20))
  }
  return service.getContractVerificationJob(jobId)
}

describe('contract API service methods', () => {
  it('returns contract detail/code/created-by and verification state transitions', async () => {
    const store = new ExplorerStore({ dbPath: tempDbPath() })
    store.upsertContractProfile({
      address: ADDRESS,
      accountType: 'contract',
      creatorAddress: 'anim1creator000000000000000000000000000abc',
      creatorTxHash: CREATOR_TX,
      creationBlockHeight: 42,
      creationBlockHash: '0x' + '4'.repeat(64),
      creationTimestamp: 1_700_000_042,
      runtimeCodeHash: hashHex(RUNTIME),
      codeHash: hashHex(CREATION)
    })

    const verifier = new ContractVerifier(store, {
      resolveOnChainMaterial: async (address) =>
        address === ADDRESS
          ? {
              address,
              creationBytecode: CREATION,
              creationBytecodeHash: hashHex(CREATION),
              runtimeBytecode: RUNTIME,
              runtimeBytecodeHash: hashHex(RUNTIME),
              onChainCodeHash: hashHex(RUNTIME),
              constructorArgs: null
            }
          : null,
      resolveDefaultAbi: async () => [{ type: 'function', name: 'set', inputs: [{ name: 'value', type: 'uint256' }], outputs: [] }]
    })

    const service = new ExplorerService(
      {
        getHead: async () => ({ height: 50, hash: '0x' + 'f'.repeat(64), time: 1_700_000_050 }),
        getBlockByNumber: async () => ({ header: { height: 50, hash: '0x' + 'a'.repeat(64), time: 1_700_000_050 }, txs: [], receipts: [] }),
        getBlockByHash: async () => ({ header: { height: 50, hash: '0x' + 'a'.repeat(64), time: 1_700_000_050 }, txs: [], receipts: [] }),
        getTransactionByHash: async () => ({ hash: CREATOR_TX, from: 'anim1creator', to: null, code: CREATION, blockNumber: 42, blockHash: '0x' + '4'.repeat(64) }),
        getTransactionReceipt: async () => ({ txHash: CREATOR_TX, blockNumber: 42, blockHash: '0x' + '4'.repeat(64), status: 'SUCCESS', contractAddress: ADDRESS }),
        getMempoolPending: async () => [],
        getMempoolStats: async () => ({ count: 0, totalBytes: 0, oldestAgeSec: null }),
        getPeers: async () => [],
        getBalance: async () => '0x0',
        getAccount: async () => ({ nonce: 1, balance: '0x0' }),
        getCode: async (address: string) => (address === ADDRESS ? RUNTIME : null)
      },
      { store, verifier }
    )

    const detail = await service.getContractDetail(ADDRESS, 20)
    expect(detail.profile.accountType).toBe('contract')
    expect(detail.profile.creatorTxHash).toBe(CREATOR_TX)

    const code = await service.getContractCode(ADDRESS)
    expect(code.codeHash).toBe(hashHex(RUNTIME))

    const byCreationTx = await service.getContractByCreationTx(CREATOR_TX)
    expect(byCreationTx?.address).toBe(ADDRESS)

    const preVerification = service.getContractVerification(ADDRESS)
    expect(preVerification).toBeUndefined()

    const submit = await service.submitContractVerification({
      address: ADDRESS,
      language: 'solidity',
      sourceCode: 'contract RouteTest {}',
      buildArtifact: {
        runtimeBytecode: RUNTIME,
        creationBytecode: CREATION,
        compilerVersion: '0.8.24'
      }
    })
    const finalJob = await waitForJob(service, submit.jobId)
    expect(finalJob?.status).toBe('verified')

    const verification = service.getContractVerification(ADDRESS)
    expect(verification?.status).toBe('verified')
    expect(verification?.sourceFiles).toBeTruthy()
  })
})
