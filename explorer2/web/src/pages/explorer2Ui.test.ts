import { describe, expect, it } from 'vitest'
import { txTypeLabel } from './TxDetailPage'
import { contractTabsForAccountType, functionSignature, parseAbiFunctions } from './AddressPage'

describe('explorer2 UI helpers', () => {
  it('maps transaction classification badge labels', () => {
    expect(txTypeLabel('native_transfer')).toBe('Native Transfer')
    expect(txTypeLabel('contract_deployment')).toBe('Contract Deployment')
    expect(txTypeLabel('contract_interaction')).toBe('Contract Call')
    expect(txTypeLabel('unknown')).toBe('Unknown')
  })

  it('returns contract tabs only for contract accounts', () => {
    expect(contractTabsForAccountType('contract')).toEqual(['overview', 'code', 'verification', 'read', 'write', 'events'])
    expect(contractTabsForAccountType('eoa')).toEqual(['overview'])
    expect(contractTabsForAccountType('unknown')).toEqual(['overview'])
  })

  it('extracts ABI functions and renders signatures', () => {
    const abi = [
      { type: 'function', name: 'totalSupply', inputs: [], outputs: [{ type: 'uint256' }], stateMutability: 'view' },
      { type: 'function', name: 'transfer', inputs: [{ type: 'address' }, { type: 'uint256' }], outputs: [], stateMutability: 'nonpayable' },
      { type: 'event', name: 'Transfer', inputs: [] }
    ]
    const functions = parseAbiFunctions(abi)
    expect(functions).toHaveLength(2)
    expect(functionSignature(functions[0])).toBe('totalSupply()')
    expect(functionSignature(functions[1])).toBe('transfer(address, uint256)')
  })
})
