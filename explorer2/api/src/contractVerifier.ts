import { createHash, randomUUID } from 'node:crypto'
import { spawnSync } from 'node:child_process'
import * as cbor from 'cbor'
import type {
  ContractVerificationJob,
  ContractVerificationRecord,
  ContractVerificationSubmitRequest
} from '@animica/explorer2-shared'
import { ExplorerStore } from './explorerStore.js'

export interface OnChainContractMaterial {
  address: string
  creationBytecode?: string | null
  creationBytecodeHash?: string | null
  runtimeBytecode?: string | null
  runtimeBytecodeHash?: string | null
  onChainCodeHash?: string | null
  constructorArgs?: string | null
}

export interface ContractVerifierContext {
  resolveOnChainMaterial: (address: string) => Promise<OnChainContractMaterial | null>
  resolveDefaultAbi: (address: string) => Promise<unknown>
}

interface CompiledArtifact {
  runtimeBytecode?: string | null
  runtimeBytecodeHash?: string | null
  creationBytecode?: string | null
  creationBytecodeHash?: string | null
  compilerVersion?: string | null
  diagnostics: string[]
}

function nowTs(): number {
  return Math.floor(Date.now() / 1000)
}

function normalizeHex(value: string | null | undefined): string | null {
  if (!value || typeof value !== 'string') return null
  const trimmed = value.trim().toLowerCase()
  if (!trimmed.length) return null
  if (trimmed.startsWith('0x')) return trimmed
  if (/^[0-9a-f]+$/i.test(trimmed)) return `0x${trimmed}`
  return null
}

function sha3Hex(buffer: Buffer): string {
  return `0x${createHash('sha3-256').update(buffer).digest('hex')}`
}

function decodeHex(value: string): Buffer | null {
  const normalized = normalizeHex(value)
  if (!normalized) return null
  const body = normalized.slice(2)
  if (body.length % 2 !== 0 || /[^0-9a-f]/i.test(body)) return null
  return Buffer.from(body, 'hex')
}

function extractSourceFiles(request: ContractVerificationSubmitRequest): Record<string, string> {
  if (request.sources && typeof request.sources === 'object' && Object.keys(request.sources).length > 0) {
    return request.sources
  }
  if (typeof request.sourceCode === 'string' && request.sourceCode.length > 0) {
    return { 'contract.py': request.sourceCode }
  }
  return {}
}

function validateSources(sources: Record<string, string>): string[] {
  const errors: string[] = []
  const files = Object.entries(sources)
  if (!files.length) {
    errors.push('missing source code')
    return errors
  }
  if (files.length > 64) {
    errors.push('too many source files (max 64)')
  }
  for (const [name, code] of files) {
    if (name.length > 256) errors.push(`file name too long: ${name}`)
    if (name.startsWith('/') || name.includes('..') || name.includes('\\')) {
      errors.push(`invalid source path: ${name}`)
    }
    if (typeof code !== 'string' || code.length === 0) {
      errors.push(`empty source content: ${name}`)
      continue
    }
    if (code.length > 512_000) {
      errors.push(`source file exceeds 512KB: ${name}`)
    }
  }
  return errors
}

function validateCompilerVersion(version: string | undefined): boolean {
  if (!version) return true
  return /^[a-z0-9._+\-]+$/i.test(version)
}

function compileWithVmPy(primarySource: string, contractName: string): CompiledArtifact {
  const py = `
import json,sys,hashlib
from vm_py.runtime.loader import compile_source_to_ir
import vm_py
payload=json.loads(sys.stdin.read() or "{}")
src=payload.get("source","")
name=payload.get("name","contract")
ir, _mod, _gas = compile_source_to_ir(src, name_hint=name)
out={
  "runtimeBytecode":"0x"+ir.hex(),
  "runtimeBytecodeHash":"0x"+hashlib.sha3_256(ir).hexdigest(),
  "compilerVersion":getattr(vm_py,"__version__","unknown"),
}
print(json.dumps(out))
`

  const result = spawnSync('python3', ['-c', py], {
    input: JSON.stringify({ source: primarySource, name: contractName }),
    encoding: 'utf-8'
  })

  if (result.status !== 0) {
    return {
      runtimeBytecode: null,
      runtimeBytecodeHash: null,
      compilerVersion: null,
      diagnostics: [`vm_py compile failed: ${result.stderr || result.stdout || 'unknown error'}`]
    }
  }

  try {
    const payload = JSON.parse(result.stdout || '{}') as {
      runtimeBytecode?: string
      runtimeBytecodeHash?: string
      compilerVersion?: string
    }
    return {
      runtimeBytecode: normalizeHex(payload.runtimeBytecode || ''),
      runtimeBytecodeHash: normalizeHex(payload.runtimeBytecodeHash || ''),
      compilerVersion: payload.compilerVersion || null,
      diagnostics: []
    }
  } catch {
    return {
      runtimeBytecode: null,
      runtimeBytecodeHash: null,
      compilerVersion: null,
      diagnostics: ['vm_py compile returned invalid JSON']
    }
  }
}

function compileFallback(primarySource: string, sources: Record<string, string>, metadataJson: unknown): CompiledArtifact {
  const runtime = Buffer.from(primarySource, 'utf-8')
  let creation = Buffer.from(runtime)
  try {
    const encoded = cbor.Encoder.encodeCanonical({
      sources,
      metadata: metadataJson ?? null
    }) as Uint8Array
    creation = Buffer.from(encoded)
  } catch {
    creation = Buffer.from(runtime)
  }
  return {
    runtimeBytecode: `0x${runtime.toString('hex')}`,
    runtimeBytecodeHash: sha3Hex(runtime),
    creationBytecode: `0x${creation.toString('hex')}`,
    creationBytecodeHash: sha3Hex(creation),
    compilerVersion: 'fallback',
    diagnostics: ['fallback compiler path used']
  }
}

function compareBytecode(params: {
  compiled: CompiledArtifact
  onChain: OnChainContractMaterial
}): { ok: boolean; reason?: string } {
  const candidates = new Set<string>()
  for (const value of [
    params.compiled.runtimeBytecodeHash,
    params.compiled.creationBytecodeHash
  ]) {
    if (value) candidates.add(value)
  }
  for (const blob of [params.compiled.runtimeBytecode, params.compiled.creationBytecode]) {
    const bytes = blob ? decodeHex(blob) : null
    if (bytes) candidates.add(sha3Hex(bytes))
  }

  const onChainCreation = normalizeHex(params.onChain.creationBytecodeHash || undefined)
  const onChainRuntime = normalizeHex(params.onChain.runtimeBytecodeHash || params.onChain.onChainCodeHash || undefined)

  if (!onChainCreation && !onChainRuntime) {
    return { ok: false, reason: 'missing on-chain bytecode/hash for comparison' }
  }
  if (onChainCreation && !candidates.has(onChainCreation)) {
    return { ok: false, reason: 'creation bytecode hash mismatch' }
  }
  if (onChainRuntime && !candidates.has(onChainRuntime)) {
    return { ok: false, reason: 'runtime bytecode hash mismatch' }
  }
  return { ok: true }
}

export class ContractVerifier {
  private inFlight = new Map<string, Promise<void>>()

  constructor(private store: ExplorerStore, private context: ContractVerifierContext) {}

  async submit(request: ContractVerificationSubmitRequest): Promise<ContractVerificationJob> {
    const jobId = randomUUID()
    this.store.createVerificationJob({
      jobId,
      address: request.address,
      requestJson: request
    })

    const runner = this.runVerification(jobId, request)
    this.inFlight.set(jobId, runner)
    void runner.finally(() => this.inFlight.delete(jobId))

    const job = this.store.getVerificationJob(jobId)
    if (!job) throw new Error('failed to create verification job')
    return job
  }

  getJob(jobId: string): ContractVerificationJob | null {
    return this.store.getVerificationJob(jobId)
  }

  private async runVerification(jobId: string, request: ContractVerificationSubmitRequest): Promise<void> {
    this.store.updateVerificationJob({ jobId, status: 'running' })

    try {
      const sources = extractSourceFiles(request)
      const sourceErrors = validateSources(sources)
      if (sourceErrors.length) {
        throw new Error(sourceErrors.join('; '))
      }
      if (!validateCompilerVersion(request.compilerVersion)) {
        throw new Error('invalid compiler version format')
      }

      const onChain = await this.context.resolveOnChainMaterial(request.address)
      if (!onChain) throw new Error('contract address is not indexed or not a contract')

      const contractName = request.contractName || 'Contract'
      const primarySource = Object.values(sources)[0] || ''
      const language = String(request.language || '').toLowerCase()

      let compiled: CompiledArtifact
      if (language === 'python' || language === 'python-vm' || language === 'animica-python-vm') {
        if (request.optimizationEnabled) {
          throw new Error('optimization settings mismatch: python-vm verifier does not support optimizer')
        }
        compiled = compileWithVmPy(primarySource, contractName)
        if (!compiled.runtimeBytecodeHash) {
          const fallback = compileFallback(primarySource, sources, request.metadataJson)
          compiled = {
            ...fallback,
            diagnostics: [...compiled.diagnostics, ...fallback.diagnostics]
          }
        } else {
          const creation = Buffer.from(cbor.Encoder.encodeCanonical({
            sources,
            metadata: request.metadataJson ?? null
          }) as Uint8Array)
          compiled.creationBytecode = `0x${creation.toString('hex')}`
          compiled.creationBytecodeHash = sha3Hex(creation)
        }
      } else {
        const buildArtifact = request.buildArtifact as Record<string, unknown> | undefined
        const runtime = normalizeHex(String(buildArtifact?.runtimeBytecode || buildArtifact?.deployedBytecode || ''))
        const creation = normalizeHex(String(buildArtifact?.creationBytecode || buildArtifact?.bytecode || ''))
        if (!runtime && !creation) {
          throw new Error(`unsupported language/compiler '${request.language}' without build artifact bytecode`)
        }
        const runtimeBytes = runtime ? decodeHex(runtime) : null
        const creationBytes = creation ? decodeHex(creation) : null
        compiled = {
          runtimeBytecode: runtime,
          runtimeBytecodeHash: runtimeBytes ? sha3Hex(runtimeBytes) : null,
          creationBytecode: creation,
          creationBytecodeHash: creationBytes ? sha3Hex(creationBytes) : null,
          compilerVersion: typeof buildArtifact?.compilerVersion === 'string' ? buildArtifact.compilerVersion : request.compilerVersion || null,
          diagnostics: []
        }
      }

      if (request.compilerVersion && compiled.compilerVersion && request.compilerVersion !== compiled.compilerVersion) {
        throw new Error(`compiler version mismatch: expected ${request.compilerVersion}, got ${compiled.compilerVersion}`)
      }

      if (request.buildArtifact && typeof request.buildArtifact === 'object') {
        const artifact = request.buildArtifact as Record<string, unknown>
        if (typeof artifact.optimizationEnabled === 'boolean' && request.optimizationEnabled !== undefined && artifact.optimizationEnabled !== request.optimizationEnabled) {
          throw new Error('optimization settings mismatch')
        }
        if (typeof artifact.optimizationRuns === 'number' && request.optimizationRuns !== undefined && artifact.optimizationRuns !== request.optimizationRuns) {
          throw new Error('optimization settings mismatch')
        }
      }

      const submittedConstructor = normalizeHex(request.constructorArgs || undefined)
      const chainConstructor = normalizeHex(onChain.constructorArgs || undefined)
      if (submittedConstructor && chainConstructor && submittedConstructor !== chainConstructor) {
        throw new Error('constructor args mismatch')
      }
      if (submittedConstructor && !chainConstructor) {
        throw new Error('constructor args mismatch: chain has no constructor args')
      }

      const compare = compareBytecode({ compiled, onChain })
      if (!compare.ok) {
        throw new Error(compare.reason || 'bytecode mismatch')
      }

      const resolvedAbi = request.abi ?? (await this.context.resolveDefaultAbi(request.address))
      const now = nowTs()
      const result: ContractVerificationRecord = {
        status: 'verified',
        verifierStatus: 'match',
        contractName,
        language: request.language,
        compiler: request.compiler ?? 'vm_py',
        compilerVersion: compiled.compilerVersion ?? request.compilerVersion ?? null,
        optimizationEnabled: request.optimizationEnabled ?? false,
        optimizationRuns: request.optimizationRuns ?? null,
        vmTarget: request.vmTarget ?? null,
        constructorArgs: submittedConstructor ?? chainConstructor ?? null,
        metadataJson: request.metadataJson ?? null,
        sourceFiles: sources,
        abi: resolvedAbi ?? null,
        creationBytecodeHash: compiled.creationBytecodeHash ?? onChain.creationBytecodeHash ?? null,
        runtimeBytecodeHash: compiled.runtimeBytecodeHash ?? onChain.runtimeBytecodeHash ?? onChain.onChainCodeHash ?? null,
        verifiedAt: now,
        submittedAt: now,
        completedAt: now,
        error: null
      }

      this.store.upsertContractProfile({
        address: request.address,
        accountType: 'contract',
        abi: result.abi,
        metadataJson: result.metadataJson,
        runtimeCodeHash: result.runtimeBytecodeHash ?? undefined,
        codeHash: result.creationBytecodeHash ?? undefined
      })
      this.store.updateVerificationJob({
        jobId,
        status: 'verified',
        result
      })
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error)
      this.store.updateVerificationJob({
        jobId,
        status: 'failed',
        error: message,
        result: {
          status: 'failed',
          verifierStatus: 'mismatch',
          error: message,
          submittedAt: nowTs(),
          completedAt: nowTs()
        }
      })
    }
  }
}
