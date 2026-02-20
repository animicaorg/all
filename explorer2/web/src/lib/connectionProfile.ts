/**
 * Connection Profile management for Explorer2.
 * Profiles are stored in localStorage; each has a name, RPC URL, and optional chain ID.
 */

import { normalizeRpcUrl } from './rpcUtils'

export type ProfileName = 'mainnet' | 'local' | 'custom'

export interface ConnectionProfile {
  name: ProfileName
  label: string
  rpcUrl: string
  chainId?: number
}

const MAINNET_PROFILE: ConnectionProfile = {
  name: 'mainnet',
  label: 'Mainnet Remote',
  rpcUrl: 'https://mainnet.animica.org/rpc',
  chainId: 1,
}

const LOCAL_PROFILE: ConnectionProfile = {
  name: 'local',
  label: 'Local Node',
  rpcUrl: 'http://127.0.0.1:8545/rpc',
}

const STORAGE_KEY = 'explorer2_connection_profile'

export function getBuiltinProfiles(): ConnectionProfile[] {
  return [MAINNET_PROFILE, LOCAL_PROFILE]
}

export function getCurrentProfile(): ConnectionProfile {
  try {
    const stored = localStorage.getItem(STORAGE_KEY)
    if (stored) {
      const parsed = JSON.parse(stored) as ConnectionProfile
      if (parsed.rpcUrl) {
        const norm = normalizeRpcUrl(parsed.rpcUrl)
        parsed.rpcUrl = norm.url
        return parsed
      }
    }
  } catch {
    // Ignore storage errors
  }
  // Default to mainnet
  return MAINNET_PROFILE
}

export function setProfile(profile: ConnectionProfile): void {
  try {
    const norm = normalizeRpcUrl(profile.rpcUrl)
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ ...profile, rpcUrl: norm.url }))
  } catch {
    // Ignore storage errors
  }
}

export function setCustomProfile(rpcUrl: string, chainId?: number): ConnectionProfile {
  const norm = normalizeRpcUrl(rpcUrl)
  const profile: ConnectionProfile = {
    name: 'custom',
    label: 'Custom',
    rpcUrl: norm.url,
    chainId,
  }
  setProfile(profile)
  return profile
}
