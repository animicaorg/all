import { createHash } from 'node:crypto'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { describe, expect, it } from 'vitest'
import { ContractVerifier } from '../src/contractVerifier'
import { ExplorerStore } from '../src/explorerStore'

const ADDRESS = 'anim1verified000000000000000000000000000000test'
const RUNTIME = '0x6001600255'
const CREATION = '0x60056001600255'
const CONSTRUCTOR_ARGS = '0x1234'

function tempDbPath(): string {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'explorer2-verify-'))
  return path.join(dir, 'explorer2-index.db')
}

function hashHex(value: string): string {
  const normalized = value.startsWith('0x') ? value.slice(2) : value
  return `0x${createHash('sha3-256').update(Buffer.from(normalized, 'hex')).digest('hex')}`
}

async function waitForJob(verifier: ContractVerifier, jobId: string): Promise<ReturnType<ContractVerifier['getJob']>> {
  for (let i = 0; i < 80; i += 1) {
    const job = verifier.getJob(jobId)
    if (job && (job.status === 'verified' || job.status === 'failed')) {
      return job
    }
    await new Promise((resolve) => setTimeout(resolve, 20))
  }
  return verifier.getJob(jobId)
}

function makeVerifier() {
  const store = new ExplorerStore({ dbPath: tempDbPath() })
  store.upsertContractProfile({
    address: ADDRESS,
    accountType: 'contract',
    codeHash: hashHex(CREATION),
    runtimeCodeHash: hashHex(RUNTIME)
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
            constructorArgs: CONSTRUCTOR_ARGS
          }
        : null,
    resolveDefaultAbi: async () => [{ type: 'function', name: 'set', inputs: [{ name: 'value', type: 'uint256' }], outputs: [] }]
  })
  return { store, verifier }
}

describe('contract verification flow', () => {
  it('verifies matching source/settings and persists artifacts', async () => {
    const { store, verifier } = makeVerifier()
    const job = await verifier.submit({
      address: ADDRESS,
      contractName: 'Counter',
      language: 'solidity',
      compiler: 'solc',
      compilerVersion: '0.8.24',
      optimizationEnabled: true,
      optimizationRuns: 200,
      constructorArgs: CONSTRUCTOR_ARGS,
      sourceCode: 'contract Counter { uint x; function set(uint v) public { x = v; } }',
      buildArtifact: {
        runtimeBytecode: RUNTIME,
        creationBytecode: CREATION,
        compilerVersion: '0.8.24',
        optimizationEnabled: true,
        optimizationRuns: 200
      }
    })

    const result = await waitForJob(verifier, job.jobId)
    expect(result?.status).toBe('verified')
    expect(result?.result?.runtimeBytecodeHash).toBe(hashHex(RUNTIME))
    expect(result?.result?.creationBytecodeHash).toBe(hashHex(CREATION))

    const persisted = store.getLatestVerificationForAddress(ADDRESS)
    expect(persisted?.status).toBe('verified')
    expect(persisted?.sourceFiles && Object.keys(persisted.sourceFiles).length).toBeGreaterThan(0)
    expect(persisted?.abi).toBeTruthy()
  })

  it('fails verification on wrong compiler version', async () => {
    const { verifier } = makeVerifier()
    const job = await verifier.submit({
      address: ADDRESS,
      language: 'solidity',
      compilerVersion: '0.8.25',
      sourceCode: 'contract X {}',
      buildArtifact: {
        runtimeBytecode: RUNTIME,
        creationBytecode: CREATION,
        compilerVersion: '0.8.24'
      }
    })
    const result = await waitForJob(verifier, job.jobId)
    expect(result?.status).toBe('failed')
    expect(result?.error?.toLowerCase()).toContain('compiler version mismatch')
  })

  it('fails verification on wrong optimization settings', async () => {
    const { verifier } = makeVerifier()
    const job = await verifier.submit({
      address: ADDRESS,
      language: 'solidity',
      optimizationEnabled: false,
      optimizationRuns: 100,
      sourceCode: 'contract X {}',
      buildArtifact: {
        runtimeBytecode: RUNTIME,
        creationBytecode: CREATION,
        compilerVersion: '0.8.24',
        optimizationEnabled: true,
        optimizationRuns: 100
      }
    })
    const result = await waitForJob(verifier, job.jobId)
    expect(result?.status).toBe('failed')
    expect(result?.error?.toLowerCase()).toContain('optimization settings mismatch')
  })

  it('fails verification on wrong constructor args', async () => {
    const { verifier } = makeVerifier()
    const job = await verifier.submit({
      address: ADDRESS,
      language: 'solidity',
      constructorArgs: '0xabcd',
      sourceCode: 'contract X {}',
      buildArtifact: {
        runtimeBytecode: RUNTIME,
        creationBytecode: CREATION,
        compilerVersion: '0.8.24'
      }
    })
    const result = await waitForJob(verifier, job.jobId)
    expect(result?.status).toBe('failed')
    expect(result?.error?.toLowerCase()).toContain('constructor args mismatch')
  })

  it('fails verification on mismatched source/bytecode', async () => {
    const { verifier } = makeVerifier()
    const job = await verifier.submit({
      address: ADDRESS,
      language: 'solidity',
      sourceCode: 'contract NotCounter {}',
      buildArtifact: {
        runtimeBytecode: '0x6002600255',
        creationBytecode: CREATION,
        compilerVersion: '0.8.24'
      }
    })
    const result = await waitForJob(verifier, job.jobId)
    expect(result?.status).toBe('failed')
    expect(result?.error?.toLowerCase()).toContain('runtime bytecode hash mismatch')
  })
})
