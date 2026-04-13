import { useEffect, useMemo, useState } from 'react'
import type { Dispatch, SetStateAction } from 'react'
import { Link, useParams } from 'react-router-dom'
import type { AddressSummary, ContractCodeResponse, ContractVerificationJob, ContractVerificationRecord, TxSummary } from '@animica/explorer2-shared'
import { api } from '../lib/api'
import { formatBalance, formatNumber, shorten } from '../lib/format'
import CopyButton from '../components/CopyButton'
import Skeleton from '../components/Skeleton'

type ContractTab = 'overview' | 'code' | 'verification' | 'read' | 'write' | 'events'

interface VerificationFormState {
  contractName: string
  language: string
  compiler: string
  compilerVersion: string
  optimizationEnabled: boolean
  optimizationRuns: string
  vmTarget: string
  constructorArgs: string
  sourceCode: string
  sourcesJson: string
  abiJson: string
  metadataJson: string
  buildArtifactJson: string
}

const INITIAL_VERIFICATION_FORM: VerificationFormState = {
  contractName: '',
  language: 'python-vm',
  compiler: 'vm_py',
  compilerVersion: '',
  optimizationEnabled: false,
  optimizationRuns: '',
  vmTarget: '',
  constructorArgs: '',
  sourceCode: '',
  sourcesJson: '',
  abiJson: '',
  metadataJson: '',
  buildArtifactJson: ''
}

export function contractTabsForAccountType(accountType?: string): ContractTab[] {
  if (accountType === 'contract') {
    return ['overview', 'code', 'verification', 'read', 'write', 'events']
  }
  return ['overview']
}

function classificationLabel(value?: string): string {
  if (value === 'contract_deployment') return 'Contract Deployment'
  if (value === 'contract_interaction') return 'Contract Call'
  if (value === 'native_transfer') return 'Native Transfer'
  return 'Unknown'
}

function parseJsonField<T>(value: string, field: string): T | undefined {
  if (!value.trim()) return undefined
  try {
    return JSON.parse(value) as T
  } catch {
    throw new Error(`${field} is not valid JSON`)
  }
}

export function parseAbiFunctions(abi: unknown): Array<{ name: string; stateMutability?: string; inputs?: Array<{ name?: string; type?: string }> }> {
  const root = abi && typeof abi === 'object' && 'abi' in (abi as Record<string, unknown>)
    ? (abi as Record<string, unknown>).abi
    : abi
  if (!Array.isArray(root)) return []
  return root
    .filter((entry) => entry && typeof entry === 'object' && String((entry as any).type || 'function').toLowerCase() === 'function')
    .map((entry) => entry as { name?: string; stateMutability?: string; inputs?: Array<{ name?: string; type?: string }> })
    .filter((entry) => typeof entry.name === 'string')
    .map((entry) => ({
      name: entry.name as string,
      stateMutability: entry.stateMutability,
      inputs: Array.isArray(entry.inputs) ? entry.inputs : []
    }))
}

export function functionSignature(fn: { name: string; inputs?: Array<{ name?: string; type?: string }> }): string {
  const args = (fn.inputs ?? []).map((input) => input.type || 'unknown').join(', ')
  return `${fn.name}(${args})`
}

export default function AddressPage() {
  const { address } = useParams()
  const [summary, setSummary] = useState<AddressSummary | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [activeTab, setActiveTab] = useState<ContractTab>('overview')
  const [contractCode, setContractCode] = useState<ContractCodeResponse | null>(null)
  const [verification, setVerification] = useState<ContractVerificationRecord | null>(null)
  const [verificationJob, setVerificationJob] = useState<ContractVerificationJob | null>(null)
  const [form, setForm] = useState<VerificationFormState>(INITIAL_VERIFICATION_FORM)
  const [verificationError, setVerificationError] = useState<string | null>(null)
  const [verificationSubmitting, setVerificationSubmitting] = useState(false)

  const isContract = summary?.accountType === 'contract'
  const verified = verification?.status === 'verified'

  const load = async (cursor?: string | null) => {
    if (!address) return
    setLoading(true)
    try {
      const res = await api.getAddress(address, 15, cursor ?? undefined)
      setSummary((prev) => {
        if (!prev || !cursor) return res
        return {
          ...res,
          txs: [...prev.txs, ...res.txs]
        }
      })
      setError(null)
    } catch (err) {
      setError(String(err))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    setActiveTab('overview')
    setContractCode(null)
    setVerification(null)
    setVerificationJob(null)
    setVerificationError(null)
    setForm(INITIAL_VERIFICATION_FORM)
    void load()
  }, [address])

  useEffect(() => {
    if (!address || !isContract) return
    const initial = summary?.contract?.verification ?? null
    if (initial) {
      setVerification(initial)
      return
    }
    api.getContractVerification(address)
      .then((value) => setVerification(value))
      .catch((err) => setVerificationError(String(err)))
  }, [address, isContract, summary?.contract?.verification])

  useEffect(() => {
    if (!address || !isContract || activeTab !== 'code') return
    api.getContractCode(address)
      .then((value) => setContractCode(value))
      .catch((err) => setVerificationError(String(err)))
  }, [activeTab, address, isContract])

  useEffect(() => {
    if (!verificationJob || verificationJob.status === 'verified' || verificationJob.status === 'failed') return
    const interval = setInterval(() => {
      api.getContractVerificationJob(verificationJob.jobId)
        .then((job) => {
          setVerificationJob(job)
          if (job.status === 'verified') {
            if (address) {
              api.getContractVerification(address).then((value) => setVerification(value)).catch(() => undefined)
              void load()
            }
          }
        })
        .catch(() => undefined)
    }, 2000)
    return () => clearInterval(interval)
  }, [verificationJob, address])

  const abiFunctions = useMemo(() => parseAbiFunctions(verification?.abi ?? summary?.contract?.abi ?? null), [verification?.abi, summary?.contract?.abi])
  const readFunctions = abiFunctions.filter((fn) => {
    const mut = String(fn.stateMutability || '').toLowerCase()
    return mut === 'view' || mut === 'pure' || mut === 'readonly'
  })
  const writeFunctions = abiFunctions.filter((fn) => !readFunctions.includes(fn))

  const submitVerification = async () => {
    if (!address) return
    setVerificationSubmitting(true)
    setVerificationError(null)
    try {
      const sources = parseJsonField<Record<string, string>>(form.sourcesJson, 'sources')
      const abi = parseJsonField<unknown>(form.abiJson, 'ABI')
      const metadataJson = parseJsonField<unknown>(form.metadataJson, 'metadata')
      const buildArtifact = parseJsonField<unknown>(form.buildArtifactJson, 'build artifact')

      const payload = {
        address,
        contractName: form.contractName || undefined,
        language: form.language,
        compiler: form.compiler || undefined,
        compilerVersion: form.compilerVersion || undefined,
        optimizationEnabled: form.optimizationEnabled,
        optimizationRuns: form.optimizationRuns ? Number(form.optimizationRuns) : undefined,
        vmTarget: form.vmTarget || undefined,
        constructorArgs: form.constructorArgs || undefined,
        sourceCode: form.sourceCode || undefined,
        sources,
        abi,
        metadataJson,
        buildArtifact
      }

      const hasSource = Boolean(payload.sourceCode) || (payload.sources && Object.keys(payload.sources).length > 0)
      if (!hasSource) {
        throw new Error('Provide sourceCode or sources JSON')
      }

      const job = await api.submitContractVerification(payload)
      setVerificationJob(job)
    } catch (err) {
      setVerificationError(err instanceof Error ? err.message : String(err))
    } finally {
      setVerificationSubmitting(false)
    }
  }

  if (error) {
    return (
      <div className="rounded-xl border border-red-500/40 bg-red-500/10 p-6 text-sm text-red-100">
        Failed to load address. {error}
      </div>
    )
  }

  if (!summary) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-8 w-48" />
        <Skeleton className="h-32" />
      </div>
    )
  }

  return (
    <div className="space-y-6">
      <div className="rounded-xl border border-day-200 bg-white p-6 shadow-sm dark:border-night-800 dark:bg-night-900">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <h1 className="text-2xl font-bold text-gray-900 dark:text-slate-100">{isContract ? 'Contract' : 'Address'}</h1>
          <CopyButton value={summary.address} />
        </div>
        <p className="mt-3 break-all font-mono text-sm text-gray-600 dark:text-slate-400">{summary.address}</p>

        <div className="mt-4 flex flex-wrap gap-2">
          <span className={`inline-flex rounded-full px-3 py-1 text-xs font-semibold ${isContract ? 'bg-cyan-100 text-cyan-700 dark:bg-cyan-900/30 dark:text-cyan-300' : 'bg-indigo-100 text-indigo-700 dark:bg-indigo-900/30 dark:text-indigo-300'}`}>
            Account Type: {summary.accountType ?? 'unknown'}
          </span>
          {isContract ? (
            <span className={`inline-flex rounded-full px-3 py-1 text-xs font-semibold ${verified ? 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-300' : 'bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-300'}`}>
              {verified ? 'Verified Contract' : 'Unverified Contract'}
            </span>
          ) : null}
        </div>

        <div className="mt-6 grid gap-4 sm:grid-cols-2">
          <BalanceCard label="Confirmed Balance" value={summary.confirmedBalance} />
          <BalanceCard label="Pending Balance" value={summary.pendingBalance} />
          {isContract ? (
            <>
              <MetaField label="Contract Creator" value={summary.contract?.creatorAddress ?? '—'} linkPrefix="/address/" />
              <MetaField label="Creation Tx" value={summary.contract?.creatorTxHash ?? '—'} linkPrefix="/tx/" mono />
              <MetaField
                label="Creation Height"
                value={summary.contract?.creationBlockHeight !== null && summary.contract?.creationBlockHeight !== undefined ? String(summary.contract.creationBlockHeight) : '—'}
                linkPrefix="/block/"
              />
              <MetaField
                label="Creation Timestamp"
                value={summary.contract?.creationTimestamp ? new Date(summary.contract.creationTimestamp * 1000).toISOString() : '—'}
                mono
              />
            </>
          ) : null}
        </div>
        {summary.partial ? (
          <p className="mt-4 text-xs text-gray-500 dark:text-slate-500">
            Showing recent activity by scanning the last {formatNumber(summary.scannedBlocks ?? 0)} blocks.
          </p>
        ) : null}
      </div>

      {isContract ? (
        <div className="rounded-xl border border-day-200 bg-white shadow-sm dark:border-night-800 dark:bg-night-900">
          <div className="border-b border-day-200 px-4 py-3 dark:border-night-800">
            <div className="flex flex-wrap gap-2">
              {contractTabsForAccountType(summary.accountType).map((tab) => (
                <button
                  key={tab}
                  type="button"
                  onClick={() => setActiveTab(tab)}
                  className={`rounded-lg px-3 py-1.5 text-sm font-medium transition-colors ${
                    activeTab === tab
                      ? 'bg-animica-100 text-animica-700 dark:bg-animica-900/40 dark:text-animica-300'
                      : 'bg-day-100 text-gray-600 hover:bg-day-200 dark:bg-night-800 dark:text-slate-300 dark:hover:bg-night-700'
                  }`}
                >
                  {tab[0].toUpperCase() + tab.slice(1)}
                </button>
              ))}
            </div>
          </div>
          <div className="p-5">
            {activeTab === 'overview' ? <TxList txs={summary.txs} /> : null}
            {activeTab === 'events' ? <ContractEventsPanel txs={summary.txs} /> : null}
            {activeTab === 'code' ? (
              <CodePanel
                runtimeCodeHash={summary.contract?.runtimeCodeHash ?? null}
                creationCodeHash={summary.contract?.codeHash ?? null}
                code={contractCode?.code ?? null}
                codeHash={contractCode?.codeHash ?? null}
              />
            ) : null}
            {activeTab === 'read' ? <AbiPanel title="Read Contract" emptyLabel="No read methods available." functions={readFunctions} /> : null}
            {activeTab === 'write' ? <AbiPanel title="Write Contract" emptyLabel="No write methods available." functions={writeFunctions} /> : null}
            {activeTab === 'verification' ? (
              <div className="space-y-4">
                {verified ? <VerificationResult verification={verification} /> : null}
                {!verified ? (
                  <>
                    <button
                      type="button"
                      onClick={() => setActiveTab('verification')}
                      className="rounded-lg bg-animica-600 px-4 py-2 text-sm font-semibold text-white hover:bg-animica-700"
                    >
                      Verify Contract
                    </button>
                    <VerificationForm form={form} setForm={setForm} onSubmit={submitVerification} submitting={verificationSubmitting} />
                  </>
                ) : null}
                {verificationJob ? (
                  <div className="rounded-lg border border-day-200 bg-day-50 p-4 text-sm dark:border-night-800 dark:bg-night-800">
                    <p className="font-semibold text-gray-800 dark:text-slate-200">Verification Job: {verificationJob.status}</p>
                    <p className="mt-1 font-mono text-xs break-all">{verificationJob.jobId}</p>
                    {verificationJob.error ? <p className="mt-2 text-red-600 dark:text-red-300">{verificationJob.error}</p> : null}
                  </div>
                ) : null}
                {verificationError ? (
                  <div className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-700 dark:border-red-900/50 dark:bg-red-900/20 dark:text-red-200">
                    {verificationError}
                  </div>
                ) : null}
              </div>
            ) : null}
          </div>
        </div>
      ) : (
        <div className="rounded-xl border border-day-200 bg-white shadow-sm dark:border-night-800 dark:bg-night-900">
          <div className="border-b border-day-200 px-6 py-4 dark:border-night-800">
            <h2 className="text-lg font-semibold text-gray-900 dark:text-slate-100">Recent Transactions</h2>
          </div>
          <div className="p-5">
            <TxList txs={summary.txs} />
          </div>
        </div>
      )}

      {summary.nextCursor ? (
        <div className="flex justify-center">
          <button
            type="button"
            disabled={!summary.nextCursor || loading}
            onClick={() => summary.nextCursor && load(summary.nextCursor)}
            className="rounded-lg border border-day-300 bg-white px-6 py-2.5 text-sm font-medium text-gray-700 transition-colors hover:bg-day-50 disabled:opacity-40 dark:border-night-700 dark:bg-night-800 dark:text-slate-300 dark:hover:bg-night-700"
          >
            {loading ? 'Loading...' : 'Load More'}
          </button>
        </div>
      ) : null}
    </div>
  )
}

function BalanceCard({ label, value }: { label: string; value?: string | null }) {
  return (
    <div>
      <p className="text-xs font-medium uppercase tracking-wider text-gray-500 dark:text-slate-500">{label}</p>
      <p className="mt-2 font-mono text-lg font-semibold text-gray-900 dark:text-slate-200">
        {formatBalance(value).anm} <span className="text-base font-normal text-gray-600 dark:text-slate-400">ANM</span>
      </p>
      {value && value !== '—' ? (
        <>
          <p className="mt-1 font-mono text-xs text-gray-500 dark:text-slate-500">{formatBalance(value).nanm} nANM</p>
          <p className="mt-1 font-mono text-xs text-gray-500 dark:text-slate-500">{formatBalance(value).hex}</p>
        </>
      ) : null}
    </div>
  )
}

function MetaField({
  label,
  value,
  linkPrefix,
  mono = false
}: {
  label: string
  value: string
  linkPrefix?: string
  mono?: boolean
}) {
  const canLink = Boolean(linkPrefix && value !== '—')
  return (
    <div>
      <p className="text-xs font-medium uppercase tracking-wider text-gray-500 dark:text-slate-500">{label}</p>
      <div className={`mt-2 text-sm text-gray-700 dark:text-slate-200 ${mono ? 'font-mono break-all' : ''}`}>
        {canLink ? (
          <Link to={`${linkPrefix}${value}`} className="text-animica-600 hover:underline dark:text-animica-400">
            {value}
          </Link>
        ) : (
          value
        )}
      </div>
    </div>
  )
}

function TxList({ txs }: { txs: TxSummary[] }) {
  if (txs.length === 0) {
    return (
      <div className="py-4 text-center text-sm text-gray-500 dark:text-slate-400">
        No transactions found in the scan window.
      </div>
    )
  }
  return (
    <div className="divide-y divide-day-200 dark:divide-night-800">
      {txs.map((tx) => (
        <div key={tx.hash} className="flex flex-wrap items-center justify-between gap-3 py-3">
          <div className="min-w-0 flex-1">
            <Link to={`/tx/${tx.hash}`} className="block font-mono text-sm text-animica-600 hover:underline dark:text-animica-400">
              {shorten(tx.hash, 10, 8)}
            </Link>
            <div className="mt-1 text-xs text-gray-500 dark:text-slate-500">
              {shorten(tx.from ?? '—', 8, 6)} → {shorten(tx.to ?? '—', 8, 6)}
              <span className="ml-2">• Amount: {tx.value ? `${formatBalance(tx.value).anm} ANM` : '—'}</span>
            </div>
            <div className="mt-1 text-xs text-gray-500 dark:text-slate-500">
              {classificationLabel(tx.classification?.type)}
            </div>
          </div>
          <span className="text-xs font-medium text-gray-600 dark:text-slate-400">{tx.status ?? 'confirmed'}</span>
        </div>
      ))}
    </div>
  )
}

function ContractEventsPanel({ txs }: { txs: TxSummary[] }) {
  const interactions = txs.filter((tx) => tx.classification?.type === 'contract_interaction')
  if (interactions.length === 0) {
    return <p className="text-sm text-gray-500 dark:text-slate-400">No contract interactions in the current window.</p>
  }
  return (
    <div className="space-y-3">
      {interactions.map((tx) => (
        <div key={tx.hash} className="rounded-lg border border-day-200 p-3 dark:border-night-800">
          <Link to={`/tx/${tx.hash}`} className="font-mono text-sm text-animica-600 hover:underline dark:text-animica-400">
            {tx.hash}
          </Link>
          <p className="mt-1 text-xs text-gray-500 dark:text-slate-400">
            Selector: {tx.classification?.methodSelector ?? '—'} • Events: {tx.classification?.decodedEvents?.length ?? 0}
          </p>
        </div>
      ))}
    </div>
  )
}

function CodePanel({
  runtimeCodeHash,
  creationCodeHash,
  code,
  codeHash
}: {
  runtimeCodeHash: string | null
  creationCodeHash: string | null
  code: string | null
  codeHash: string | null
}) {
  return (
    <div className="space-y-3">
      <p className="text-sm text-gray-700 dark:text-slate-300">Creation Bytecode Hash: <span className="font-mono break-all">{creationCodeHash ?? '—'}</span></p>
      <p className="text-sm text-gray-700 dark:text-slate-300">Runtime Bytecode Hash: <span className="font-mono break-all">{runtimeCodeHash ?? codeHash ?? '—'}</span></p>
      <div className="rounded-lg border border-day-200 p-3 dark:border-night-800">
        <p className="text-xs uppercase tracking-wide text-gray-500 dark:text-slate-500">Runtime Bytecode</p>
        <pre className="mt-2 max-h-72 overflow-auto whitespace-pre-wrap break-all font-mono text-xs text-gray-700 dark:text-slate-300">{code ?? 'No bytecode available'}</pre>
      </div>
    </div>
  )
}

function AbiPanel({
  title,
  emptyLabel,
  functions
}: {
  title: string
  emptyLabel: string
  functions: Array<{ name: string; stateMutability?: string; inputs?: Array<{ name?: string; type?: string }> }>
}) {
  if (functions.length === 0) {
    return <p className="text-sm text-gray-500 dark:text-slate-400">{emptyLabel}</p>
  }
  return (
    <div>
      <h3 className="text-base font-semibold text-gray-900 dark:text-slate-100">{title}</h3>
      <div className="mt-3 space-y-2">
        {functions.map((fn) => (
          <div key={functionSignature(fn)} className="rounded-lg border border-day-200 p-3 dark:border-night-800">
            <p className="font-mono text-sm text-gray-800 dark:text-slate-200">{functionSignature(fn)}</p>
            <p className="mt-1 text-xs text-gray-500 dark:text-slate-500">Mutability: {fn.stateMutability ?? 'nonpayable'}</p>
          </div>
        ))}
      </div>
    </div>
  )
}

function VerificationResult({ verification }: { verification: ContractVerificationRecord | null }) {
  if (!verification || verification.status !== 'verified') return null
  return (
    <div className="space-y-4">
      <div className="rounded-lg border border-emerald-200 bg-emerald-50 p-4 text-sm text-emerald-800 dark:border-emerald-900/40 dark:bg-emerald-900/20 dark:text-emerald-200">
        Contract verified successfully.
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        <MetaField label="Contract Name" value={verification.contractName ?? '—'} />
        <MetaField label="Language" value={verification.language ?? '—'} />
        <MetaField label="Compiler" value={verification.compiler ?? '—'} />
        <MetaField label="Compiler Version" value={verification.compilerVersion ?? '—'} />
        <MetaField label="Optimization" value={verification.optimizationEnabled ? 'Enabled' : 'Disabled'} />
        <MetaField label="Optimization Runs" value={verification.optimizationRuns !== null && verification.optimizationRuns !== undefined ? String(verification.optimizationRuns) : '—'} />
        <MetaField label="Constructor Args" value={verification.constructorArgs ?? '—'} mono />
        <MetaField label="Verified At" value={verification.verifiedAt ? new Date(verification.verifiedAt * 1000).toISOString() : '—'} mono />
        <MetaField label="Creation Bytecode Hash" value={verification.creationBytecodeHash ?? '—'} mono />
        <MetaField label="Runtime Bytecode Hash" value={verification.runtimeBytecodeHash ?? '—'} mono />
      </div>
      <div className="rounded-lg border border-day-200 p-3 dark:border-night-800">
        <p className="text-xs uppercase tracking-wide text-gray-500 dark:text-slate-500">Source Files</p>
        <div className="mt-2 space-y-3">
          {verification.sourceFiles && Object.keys(verification.sourceFiles).length > 0 ? (
            Object.entries(verification.sourceFiles).map(([name, contents]) => (
              <div key={name}>
                <p className="font-mono text-xs text-gray-700 dark:text-slate-300">{name}</p>
                <pre className="mt-1 max-h-64 overflow-auto whitespace-pre-wrap break-all rounded bg-day-50 p-2 font-mono text-xs dark:bg-night-800">{contents}</pre>
              </div>
            ))
          ) : (
            <p className="text-sm text-gray-500 dark:text-slate-400">No source files stored.</p>
          )}
        </div>
      </div>
      <div className="rounded-lg border border-day-200 p-3 dark:border-night-800">
        <p className="text-xs uppercase tracking-wide text-gray-500 dark:text-slate-500">ABI</p>
        <pre className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap break-all rounded bg-day-50 p-2 font-mono text-xs dark:bg-night-800">
          {verification.abi ? JSON.stringify(verification.abi, null, 2) : 'No ABI'}
        </pre>
      </div>
    </div>
  )
}

function VerificationForm({
  form,
  setForm,
  onSubmit,
  submitting
}: {
  form: VerificationFormState
  setForm: Dispatch<SetStateAction<VerificationFormState>>
  onSubmit: () => Promise<void>
  submitting: boolean
}) {
  const setField = (key: keyof VerificationFormState, value: string | boolean) => {
    setForm((prev) => ({ ...prev, [key]: value }))
  }

  return (
    <div className="space-y-3 rounded-lg border border-day-200 p-4 dark:border-night-800">
      <div className="grid gap-3 sm:grid-cols-2">
        <Input label="Contract Name" value={form.contractName} onChange={(value) => setField('contractName', value)} />
        <Input label="Language" value={form.language} onChange={(value) => setField('language', value)} />
        <Input label="Compiler" value={form.compiler} onChange={(value) => setField('compiler', value)} />
        <Input label="Compiler Version" value={form.compilerVersion} onChange={(value) => setField('compilerVersion', value)} />
        <Input label="Optimization Runs" value={form.optimizationRuns} onChange={(value) => setField('optimizationRuns', value)} />
        <Input label="VM Target" value={form.vmTarget} onChange={(value) => setField('vmTarget', value)} />
        <Input label="Constructor Args (hex)" value={form.constructorArgs} onChange={(value) => setField('constructorArgs', value)} />
      </div>
      <label className="flex items-center gap-2 text-sm text-gray-700 dark:text-slate-300">
        <input type="checkbox" checked={form.optimizationEnabled} onChange={(event) => setField('optimizationEnabled', event.target.checked)} />
        Optimization enabled
      </label>
      <Textarea label="Source Code" value={form.sourceCode} onChange={(value) => setField('sourceCode', value)} rows={8} />
      <Textarea label="Sources JSON (optional multi-file map)" value={form.sourcesJson} onChange={(value) => setField('sourcesJson', value)} rows={6} />
      <Textarea label="ABI JSON (optional)" value={form.abiJson} onChange={(value) => setField('abiJson', value)} rows={5} />
      <Textarea label="Metadata JSON (optional)" value={form.metadataJson} onChange={(value) => setField('metadataJson', value)} rows={5} />
      <Textarea label="Build Artifact JSON (optional)" value={form.buildArtifactJson} onChange={(value) => setField('buildArtifactJson', value)} rows={5} />
      <button
        type="button"
        onClick={() => void onSubmit()}
        disabled={submitting}
        className="rounded-lg bg-animica-600 px-4 py-2 text-sm font-semibold text-white hover:bg-animica-700 disabled:opacity-50"
      >
        {submitting ? 'Submitting...' : 'Submit Verification'}
      </button>
    </div>
  )
}

function Input({ label, value, onChange }: { label: string; value: string; onChange: (value: string) => void }) {
  return (
    <label className="block">
      <span className="text-xs uppercase tracking-wide text-gray-500 dark:text-slate-500">{label}</span>
      <input
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className="mt-1 w-full rounded-lg border border-day-300 bg-white px-3 py-2 text-sm dark:border-night-700 dark:bg-night-900"
      />
    </label>
  )
}

function Textarea({
  label,
  value,
  onChange,
  rows
}: {
  label: string
  value: string
  onChange: (value: string) => void
  rows: number
}) {
  return (
    <label className="block">
      <span className="text-xs uppercase tracking-wide text-gray-500 dark:text-slate-500">{label}</span>
      <textarea
        value={value}
        onChange={(event) => onChange(event.target.value)}
        rows={rows}
        className="mt-1 w-full rounded-lg border border-day-300 bg-white px-3 py-2 font-mono text-xs dark:border-night-700 dark:bg-night-900"
      />
    </label>
  )
}
