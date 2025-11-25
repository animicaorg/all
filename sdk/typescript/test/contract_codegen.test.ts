import { describe, test, expect } from 'vitest'
import * as ts from 'typescript'

// Try to find a generator function from the codegen module with a tolerant probe.
async function loadGenerator(): Promise<(name: string, abi: any, opts?: any) => string> {
  const mod: any = await import('../src/contracts/codegen')

  const candidates = [
    'generateContractClass',
    'generateContractClient',
    'generateClient',
    'generate',
    'codegenContract',
    'emit'
  ]

  for (const key of candidates) {
    if (typeof mod[key] === 'function') {
      return (name: string, abi: any, opts?: any) => mod[key](name, abi, opts)
    }
    if (mod?.default && typeof mod.default[key] === 'function') {
      return (name: string, abi: any, opts?: any) => mod.default[key](name, abi, opts)
    }
  }

  // If the module exports a single default callable
  if (typeof mod.default === 'function') {
    return (name: string, abi: any, opts?: any) => mod.default(name, abi, opts)
  }

  throw new Error('No suitable generator function exported from contracts/codegen')
}

// Minimal Counter-like ABI used across the repo’s examples
const COUNTER_ABI = {
  version: '1',
  contractName: 'Counter',
  functions: [
    { name: 'inc', stateMutability: 'nonpayable', inputs: [], outputs: [] },
    { name: 'get', stateMutability: 'view', inputs: [], outputs: [{ type: 'uint64', name: 'value' }] }
  ],
  events: [
    {
      name: 'Changed',
      inputs: [{ name: 'value', type: 'uint64', indexed: false }]
    }
  ],
  errors: []
}

function transpileTypeScript(src: string): { js: string; diagnostics: ts.Diagnostic[] } {
  const out = ts.transpileModule(src, {
    reportDiagnostics: true,
    compilerOptions: {
      target: ts.ScriptTarget.ES2020,
      module: ts.ModuleKind.ESNext,
      strict: true,
      esModuleInterop: true,
      skipLibCheck: true,
      jsx: ts.JsxEmit.React
    }
  })
  return { js: out.outputText, diagnostics: out.diagnostics ?? [] }
}

describe('contracts/codegen — emits usable class stubs', () => {
  test('generates a class with expected methods and compiles cleanly', async () => {
    const gen = await loadGenerator()
    const code = gen('Counter', COUNTER_ABI, {
      // Accept a variety of common option names without depending on them
      className: 'Counter',
      namespace: '@animica/sdk',
      emitJSDoc: true,
      emitTypes: true
    })

    expect(typeof code).toBe('string')
    expect(code.length).toBeGreaterThan(100)

    // Basic text expectations
    expect(/class\s+Counter/.test(code) || /export\s+class\s+Counter/.test(code)).toBe(true)
    expect(/inc\s*\(/.test(code)).toBe(true)
    expect(/get\s*\(/.test(code)).toBe(true)
    // Should reference events somewhere
    expect(/Changed/.test(code)).toBe(true)

    // TypeScript transpilation should produce zero errors
    const { diagnostics } = transpileTypeScript(code)
    const errors = diagnostics.filter(d => d.category === ts.DiagnosticCategory.Error)
    if (errors.length) {
      const pretty = errors
        .map(d => {
          const msg = ts.flattenDiagnosticMessageText(d.messageText, '\n')
          const code = d.code
          return `TS${code}: ${msg}`
        })
        .join('\n')
      throw new Error('Generated code has TypeScript errors:\n' + pretty)
    }
  })

  test('generator is deterministic for same ABI/name', async () => {
    const gen = await loadGenerator()
    const a = gen('Counter', COUNTER_ABI, {})
    const b = gen('Counter', COUNTER_ABI, {})
    expect(a).toBe(b)
  })

  test('different contract name changes the output', async () => {
    const gen = await loadGenerator()
    const a = gen('Counter', COUNTER_ABI, {})
    const b = gen('MyCounter', COUNTER_ABI, {})
    expect(a).not.toBe(b)
    expect(/class\s+MyCounter/.test(b) || /export\s+class\s+MyCounter/.test(b)).toBe(true)
  })
})
