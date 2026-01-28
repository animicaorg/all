#!/usr/bin/env node
/**
 * Rich List Verification Script
 * 
 * This script verifies that the rich list API returns accurate balances
 * by cross-checking against the node's RPC for a sample of addresses.
 * 
 * Usage:
 *   node verify_richlist.js [--sample N] [--rpc URL] [--api URL]
 * 
 * Options:
 *   --sample N    Number of addresses to check (default: 10)
 *   --rpc URL     Node RPC URL (default: http://localhost:8545/rpc)
 *   --api URL     Explorer API URL (default: http://localhost:8081)
 */

const args = process.argv.slice(2)
const sample = parseInt(args[args.indexOf('--sample') + 1] || '10', 10)
const rpcUrl = args[args.indexOf('--rpc') + 1] || 'http://localhost:8545/rpc'
const apiUrl = args[args.indexOf('--api') + 1] || 'http://localhost:8081'

console.log('Rich List Verification')
console.log('======================')
console.log(`Sample size: ${sample}`)
console.log(`RPC URL: ${rpcUrl}`)
console.log(`API URL: ${apiUrl}`)
console.log('')

async function callRpc(method, params = []) {
  const response = await fetch(rpcUrl, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      jsonrpc: '2.0',
      id: 1,
      method,
      params
    })
  })
  
  if (!response.ok) {
    throw new Error(`RPC call failed: ${response.statusText}`)
  }
  
  const data = await response.json()
  if (data.error) {
    throw new Error(`RPC error: ${data.error.message}`)
  }
  
  return data.result
}

async function getRichList(limit, offset = 0) {
  const response = await fetch(`${apiUrl}/api/richlist?limit=${limit}&offset=${offset}`)
  if (!response.ok) {
    throw new Error(`API call failed: ${response.statusText}`)
  }
  return response.json()
}

async function verify() {
  try {
    // Fetch rich list
    console.log(`Fetching top ${sample} addresses from rich list...`)
    const richList = await getRichList(sample, 0)
    
    console.log(`Height: ${richList.height}`)
    console.log(`Total addresses: ${richList.totalAddresses}`)
    console.log('')
    
    if (!richList.items || richList.items.length === 0) {
      console.log('No addresses found in rich list.')
      return
    }
    
    // Check each address
    console.log('Verifying balances...')
    console.log('')
    
    let mismatches = 0
    let checked = 0
    
    for (const entry of richList.items.slice(0, sample)) {
      const address = entry.address
      const richListBalance = BigInt(entry.balance)
      
      // Query RPC for actual balance
      let rpcBalance
      try {
        const result = await callRpc('state.getBalance', [address, 'latest'])
        rpcBalance = BigInt(result)
      } catch (err) {
        console.log(`[${entry.rank}] ${address}`)
        console.log(`  ERROR: Failed to query RPC: ${err.message}`)
        console.log('')
        continue
      }
      
      checked++
      
      // Compare
      const match = richListBalance === rpcBalance
      if (!match) {
        mismatches++
        console.log(`[${entry.rank}] ${address}`)
        console.log(`  MISMATCH!`)
        console.log(`  Rich List: ${richListBalance} (${formatANM(richListBalance)} ANM)`)
        console.log(`  RPC:       ${rpcBalance} (${formatANM(rpcBalance)} ANM)`)
        console.log('')
      } else {
        console.log(`[${entry.rank}] ${address}`)
        console.log(`  ✓ Match: ${formatANM(richListBalance)} ANM`)
        console.log('')
      }
    }
    
    // Summary
    console.log('======================')
    console.log('Verification Summary')
    console.log('======================')
    console.log(`Checked: ${checked}`)
    console.log(`Matches: ${checked - mismatches}`)
    console.log(`Mismatches: ${mismatches}`)
    console.log('')
    
    if (mismatches > 0) {
      console.log('❌ VERIFICATION FAILED')
      process.exit(1)
    } else {
      console.log('✅ VERIFICATION PASSED')
      process.exit(0)
    }
    
  } catch (err) {
    console.error('Error:', err.message)
    process.exit(1)
  }
}

function formatANM(nanoANM) {
  const anm = Number(nanoANM) / 1e9
  return anm.toLocaleString('en-US', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 9
  })
}

verify()
