/**
 * ABI types for the Animica Python-VM contracts.
 *
 * Mirrors the structure described by spec/abi.schema.json:
 *  - functions (name, inputs, outputs, mutability flags)
 *  - events (name, inputs, anonymous?)
 *  - errors (name, inputs)
 *
 * Type shapes intentionally align with the Python SDK so codegen and cross-lang
 * examples stay consistent.
 */

//// ────────────────────────────────────────────────────────────────────────────
// Scalar & composite types
////

/** Primitive scalar kinds supported by the VM ABI. */
export type AbiScalarType =
  | { type: 'bool' }
  | { type: 'int'; bits?: number }     // default implementation-defined (e.g., 256)
  | { type: 'uint'; bits?: number }    // default implementation-defined (e.g., 256)
  | { type: 'bytes'; size?: number }   // size omitted => dynamic bytes
  | { type: 'string' }                 // UTF-8
  | { type: 'address' }                // bech32m anim1… string on the wire

/** Array (dynamic if no length) */
export type AbiArrayType = {
  type: 'array'
  items: AbiType
  length?: number
}

/** Tuple/struct-like composite */
export type AbiTupleType = {
  type: 'tuple'
  components: AbiParam[]
}

/** Full union of ABI types */
export type AbiType = AbiScalarType | AbiArrayType | AbiTupleType

/** Named parameter for functions/events/errors */
export interface AbiParam {
  name?: string
  type: AbiType
  /** Optional doc or hint fields (forward-compatible) */
  description?: string
  indexed?: boolean // only meaningful for events
}

//// ────────────────────────────────────────────────────────────────────────────
// ABI entries: functions / events / errors
////

export type StateMutability = 'pure' | 'view' | 'nonpayable' | 'payable'

export interface AbiFunction {
  kind: 'function'
  name: string
  inputs: AbiParam[]
  outputs?: AbiParam[]
  stateMutability?: StateMutability
  /** Optional gas upper bound hint for UIs (not enforced by node) */
  gasEstimate?: number
  /** Human docs */
  notice?: string
}

export interface AbiEvent {
  kind: 'event'
  name: string
  inputs: AbiParam[]
  /** If true, event name is not part of the topic filter */
  anonymous?: boolean
  notice?: string
}

export interface AbiError {
  kind: 'error'
  name: string
  inputs: AbiParam[]
  notice?: string
}

/** Complete ABI document */
export interface ContractAbi {
  /** Optional semantic version of the ABI format or contract */
  version?: string
  /** Optional contract display name */
  name?: string
  /** Functions callable via the runtime */
  functions: AbiFunction[]
  /** Events emitted by the runtime */
  events?: AbiEvent[]
  /** Revert/error signatures */
  errors?: AbiError[]
  /** Free-form metadata (tooling) */
  metadata?: Record<string, unknown>
}

//// ────────────────────────────────────────────────────────────────────────────
// Type guards & validator (lightweight, schema-inspired)
//  - Designed to be permissive and forward-compatible.
//  - Returns a list of human-readable issues instead of throwing.
//  - Suitable for client-side checks before codegen or calls.
//
////

export function isAbiType(t: unknown): t is AbiType {
  if (!t || typeof t !== 'object') return false
  const tt = t as any
  switch (tt.type) {
    case 'bool':
    case 'string':
    case 'address':
      return true
    case 'int':
    case 'uint':
      return tt.bits === undefined || (Number.isInteger(tt.bits) && tt.bits > 0 && tt.bits <= 512)
    case 'bytes':
      return tt.size === undefined || (Number.isInteger(tt.size) && tt.size >= 1 && tt.size <= 65535)
    case 'array':
      return isAbiType(tt.items) && (tt.length === undefined || (Number.isInteger(tt.length) && tt.length >= 0))
    case 'tuple':
      return Array.isArray(tt.components) && tt.components.every(isAbiParam)
    default:
      return false
  }
}

export function isAbiParam(p: unknown): p is AbiParam {
  if (!p || typeof p !== 'object') return false
  const pp = p as any
  if (!isAbiType(pp.type)) return false
  if (pp.name !== undefined && typeof pp.name !== 'string') return false
  if (pp.indexed !== undefined && typeof pp.indexed !== 'boolean') return false
  return true
}

export function isAbiFunction(f: unknown): f is AbiFunction {
  if (!f || typeof f !== 'object') return false
  const ff = f as any
  if (ff.kind !== 'function') return false
  if (typeof ff.name !== 'string') return false
  if (!Array.isArray(ff.inputs) || !ff.inputs.every(isAbiParam)) return false
  if (ff.outputs && (!Array.isArray(ff.outputs) || !ff.outputs.every(isAbiParam))) return false
  if (ff.stateMutability && !['pure', 'view', 'nonpayable', 'payable'].includes(ff.stateMutability)) return false
  if (ff.gasEstimate !== undefined && !(Number.isFinite(ff.gasEstimate) && ff.gasEstimate >= 0)) return false
  return true
}

export function isAbiEvent(e: unknown): e is AbiEvent {
  if (!e || typeof e !== 'object') return false
  const ee = e as any
  if (ee.kind !== 'event') return false
  if (typeof ee.name !== 'string') return false
  if (!Array.isArray(ee.inputs) || !ee.inputs.every(isAbiParam)) return false
  if (ee.anonymous !== undefined && typeof ee.anonymous !== 'boolean') return false
  return true
}

export function isAbiError(er: unknown): er is AbiError {
  if (!er || typeof er !== 'object') return false
  const ee = er as any
  if (ee.kind !== 'error') return false
  if (typeof ee.name !== 'string') return false
  if (!Array.isArray(ee.inputs) || !ee.inputs.every(isAbiParam)) return false
  return true
}

export function isContractAbi(a: unknown): a is ContractAbi {
  if (!a || typeof a !== 'object') return false
  const aa = a as any
  if (!Array.isArray(aa.functions) || !aa.functions.every(isAbiFunction)) return false
  if (aa.events && (!Array.isArray(aa.events) || !aa.events.every(isAbiEvent))) return false
  if (aa.errors && (!Array.isArray(aa.errors) || !aa.errors.every(isAbiError))) return false
  if (aa.name && typeof aa.name !== 'string') return false
  if (aa.version && typeof aa.version !== 'string') return false
  return true
}

/** Validate a candidate ABI; returns { ok, errors[] } without throwing. */
export function validateAbi(maybe: unknown): { ok: boolean; errors: string[] } {
  const errors: string[] = []

  function fail(msg: string) {
    errors.push(msg)
  }

  if (!maybe || typeof maybe !== 'object') {
    return { ok: false, errors: ['ABI must be an object'] }
  }
  const a = maybe as Partial<ContractAbi>
  if (!Array.isArray(a.functions)) fail('ABI.functions must be an array')
  else {
    a.functions.forEach((fn, i) => {
      if (!isAbiFunction(fn)) fail(`functions[${i}] is not a valid AbiFunction`)
    })
  }
  if (a.events) {
    if (!Array.isArray(a.events)) fail('ABI.events must be an array when present')
    else a.events.forEach((ev, i) => {
      if (!isAbiEvent(ev)) fail(`events[${i}] is not a valid AbiEvent`)
    })
  }
  if (a.errors) {
    if (!Array.isArray(a.errors)) fail('ABI.errors must be an array when present')
    else a.errors.forEach((er, i) => {
      if (!isAbiError(er)) fail(`errors[${i}] is not a valid AbiError`)
    })
  }
  if (a.version && typeof a.version !== 'string') fail('ABI.version must be a string when present')
  if (a.name && typeof a.name !== 'string') fail('ABI.name must be a string when present')

  return { ok: errors.length === 0, errors }
}

export default {
  // types
  AbiScalarType: undefined as unknown as AbiScalarType,
  AbiArrayType: undefined as unknown as AbiArrayType,
  AbiTupleType: undefined as unknown as AbiTupleType,
  AbiType: undefined as unknown as AbiType,
  AbiParam: undefined as unknown as AbiParam,
  AbiFunction: undefined as unknown as AbiFunction,
  AbiEvent: undefined as unknown as AbiEvent,
  AbiError: undefined as unknown as AbiError,
  ContractAbi: undefined as unknown as ContractAbi,
  // guards
  isAbiType,
  isAbiParam,
  isAbiFunction,
  isAbiEvent,
  isAbiError,
  isContractAbi,
  validateAbi
}
