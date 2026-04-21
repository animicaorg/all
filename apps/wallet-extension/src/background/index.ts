// Background service worker

import { loadVault, saveVault, saveState, setUnlockedVault, getUnlockedVault, lockVault, isVaultUnlocked, loadActiveWalletId, saveActiveWalletId } from '../core/storage';
import { encrypt, decrypt } from '../core/crypto/vault';
import { PermissionManager } from '../core/permissions';
import { TxStore } from '../core/tx/store';
import { createAccount } from '../core/wallets/account';
import { importWalletsJson, exportWalletsJson } from '../core/wallets/import';
import { NETWORKS } from '../types/network';
import { getEffectiveRpcUrl, getRpcUrl, resetRpcUrl, setRpcUrl, validateRpcUrl } from '../services/rpcConfig';
import { getRpcClient, recreateRpcClient } from '../services/rpcClientFactory';
import { getBalance, getBalanceDebugState, setLastPingDebug } from '../services/balanceService';
import { rpcPing } from '../services/rpcPing';
import { sendRawTxPipeline, warmPolicyCapabilities } from '../services/sendRawTx';
import type { VaultData } from '../types/vault';
import type { Account } from '../types/wallet';
import type { TxStatus, PendingTx } from '../types/tx';
import { decodeAddress } from '../core/crypto/address';
import type { WatchedToken } from '../types/token';

// Import new canonical tx module
import { buildAndSignTransaction, fetchChainContext, algIdToSchemeId } from '../tx';
import { sign } from '../core/crypto/pq';

let unlockedPassword: string | null = null;
const DEBUG_WALLET = true; // Always enabled for troubleshooting
let balanceRequestSeq = 1;
const balanceAbortByKey = new Map<string, AbortController>();

function debugLog(message: string, details?: unknown): void {
  if (!DEBUG_WALLET) return;
  console.debug(`[wallet-bg] ${message}`, details);
}



function shouldDebugTxLogs(): boolean {
  try {
    const envFlag = (import.meta as any)?.env?.VITE_DEBUG_TX;
    if (envFlag === '1' || envFlag === 'true') return true;
  } catch {}

  try {
    const g = globalThis as any;
    return g.__ANIMICA_DEBUG_TX__ === true || g.__ANIMICA_DEBUG_TX__ === '1';
  } catch {
    return false;
  }
}

function txDebugLog(message: string, payload: Record<string, unknown>): void {
  if (!shouldDebugTxLogs()) return;
  console.debug(`[wallet-bg][tx-debug] ${message}`, payload);
}


interface TxRpcDebugState {
  lastSendRequest?: {
    rpcUrl: string;
    method: string;
    params: unknown;
    requestBody: string;
    timestamp: number;
  };
  lastSendResponse?: unknown;
  lastSendError?: string | null;
  sendAttempts?: unknown[];
}

const txRpcDebugState: TxRpcDebugState = {
  lastSendRequest: undefined,
  lastSendResponse: null,
  lastSendError: null,
};

function sanitizeRpcParams(value: unknown): unknown {
  if (typeof value === 'bigint') return value.toString();
  if (value instanceof Uint8Array) return `[bytes:${value.length}]`;
  if (Array.isArray(value)) return value.map(item => sanitizeRpcParams(item));
  if (value && typeof value === 'object') {
    return Object.fromEntries(Object.entries(value as Record<string, unknown>).map(([k, v]) => [k, sanitizeRpcParams(v)]));
  }
  return value;
}

function safeJsonStringify(value: unknown): string {
  return JSON.stringify(value, (_key, currentValue) => {
    if (typeof currentValue === 'bigint') return currentValue.toString();
    if (currentValue instanceof Uint8Array) return `[bytes:${currentValue.length}]`;
    return currentValue;
  });
}

function captureSendRpcDebug(rpcUrl: string, method: string, params: unknown): void {
  const payload = {
    jsonrpc: '2.0',
    method,
    params: sanitizeRpcParams(params),
  };
  txRpcDebugState.lastSendRequest = {
    rpcUrl,
    method,
    params: payload.params,
    requestBody: JSON.stringify(payload),
    timestamp: Date.now(),
  };
  txRpcDebugState.lastSendError = null;
  console.debug('[wallet-bg][send-rpc] request', txRpcDebugState.lastSendRequest);
}

function captureSendRpcResponse(response: unknown, error: string | null = null, attempts?: unknown[]): void {
  txRpcDebugState.lastSendResponse = response;
  txRpcDebugState.lastSendError = error;
  if (attempts) txRpcDebugState.sendAttempts = attempts;
  console.debug('[wallet-bg][send-rpc] response', { response, error, attempts });
}

function parseBaseUnitAmount(value: unknown): bigint {
  if (typeof value === 'bigint') {
    if (value < 0n) throw new Error('Invalid amount: must be a non-negative base-unit integer');
    return value;
  }
  if (typeof value === 'string') {
    const trimmed = value.trim();
    if (/^\d+$/.test(trimmed)) return BigInt(trimmed);
    if (/^0x[0-9a-f]+$/i.test(trimmed)) return BigInt(trimmed);
  }
  if (typeof value === 'number' && Number.isInteger(value) && value >= 0) return BigInt(value);
  throw new Error('Invalid amount: must be a non-negative base-unit integer string');
}

function algLabel(algId: number): string {
  switch (algId) {
    case 0x1001:
      return 'Dilithium3 (0x1001)';
    case 0x1002:
      return 'SPHINCS+ SHAKE-128s (0x1002)';
    default:
      return `alg-${algId} (0x${algId.toString(16)})`;
  }
}

function summarizeRawTx(rawTx: string): Record<string, unknown> {
  const normalized = rawTx.startsWith('0x') ? rawTx.slice(2) : rawTx;
  return {
    length: rawTx.length,
    has0xPrefix: rawTx.startsWith('0x'),
    hexLength: normalized.length,
    first16BytesHex: normalized.slice(0, 32),
    last16BytesHex: normalized.slice(Math.max(0, normalized.length - 32)),
  };
}

function parseBytesData(value: unknown): Uint8Array | undefined {
  if (value === undefined || value === null) return undefined;
  if (value instanceof Uint8Array) return value;
  if (Array.isArray(value) && value.every((item) => Number.isInteger(item) && item >= 0 && item <= 255)) {
    return Uint8Array.from(value as number[]);
  }
  if (typeof value === 'string') {
    const trimmed = value.trim();
    if (trimmed === '' || trimmed === '0x' || trimmed === '0X') return new Uint8Array();
    if (!/^0x[0-9a-f]+$/i.test(trimmed)) {
      throw new Error('Invalid data: must be 0x-prefixed hex');
    }
    const hex = trimmed.slice(2);
    if (hex.length % 2 !== 0) throw new Error('Invalid data: hex length must be even');
    const bytes = new Uint8Array(hex.length / 2);
    for (let i = 0; i < hex.length; i += 2) {
      bytes[i / 2] = parseInt(hex.slice(i, i + 2), 16);
    }
    return bytes;
  }
  throw new Error('Invalid data: expected 0x-hex string or byte array');
}

function parseChainIdValue(value: unknown): number | null {
  if (typeof value === 'number' && Number.isInteger(value) && value >= 0) return value;
  if (typeof value === 'bigint' && value >= 0n && value <= BigInt(Number.MAX_SAFE_INTEGER)) return Number(value);
  if (typeof value === 'string') {
    const trimmed = value.trim();
    if (!trimmed) return null;
    try {
      if (/^0x[0-9a-f]+$/i.test(trimmed)) return Number(BigInt(trimmed));
      if (/^\d+$/.test(trimmed)) return Number(BigInt(trimmed));
    } catch {
      return null;
    }
  }
  return null;
}

function toHexChainId(chainId: number): string {
  return `0x${chainId.toString(16)}`;
}

function resolveNetworkId(input: unknown, networkConfigs: VaultData['networkConfigs']): string {
  const target = Array.isArray(input) ? input[0] : input;

  const matchByChainId = (chainId: number): string | null => {
    for (const [networkId, config] of Object.entries(networkConfigs)) {
      if (config.chainId === chainId) return networkId;
    }
    return null;
  };

  if (typeof target === 'string') {
    const trimmed = target.trim();
    if (networkConfigs[trimmed]) return trimmed;
    const chainId = parseChainIdValue(trimmed);
    if (chainId !== null) {
      const byChain = matchByChainId(chainId);
      if (byChain) return byChain;
    }
  }

  if (typeof target === 'number' || typeof target === 'bigint') {
    const chainId = parseChainIdValue(target);
    if (chainId !== null) {
      const byChain = matchByChainId(chainId);
      if (byChain) return byChain;
    }
  }

  if (target && typeof target === 'object') {
    const obj = target as Record<string, unknown>;

    const networkIdCandidate = obj.networkId ?? obj.network ?? obj.id;
    if (typeof networkIdCandidate === 'string') {
      const trimmed = networkIdCandidate.trim();
      if (networkConfigs[trimmed]) return trimmed;
      const parsed = parseChainIdValue(trimmed);
      if (parsed !== null) {
        const byChain = matchByChainId(parsed);
        if (byChain) return byChain;
      }
    } else {
      const parsed = parseChainIdValue(networkIdCandidate);
      if (parsed !== null) {
        const byChain = matchByChainId(parsed);
        if (byChain) return byChain;
      }
    }

    const chainIdCandidate = obj.chainId ?? obj.chain_id;
    const parsedChainId = parseChainIdValue(chainIdCandidate);
    if (parsedChainId !== null) {
      const byChain = matchByChainId(parsedChainId);
      if (byChain) return byChain;
    }
  }

  throw new Error('Invalid network target');
}

type WatchTokenInput = {
  type: string;
  address: string;
  symbol: string;
  decimals: number;
  name?: string;
  image?: string;
};

function parseWatchTokenParams(input: unknown): WatchTokenInput {
  const payload = Array.isArray(input) ? input[0] : input;
  if (!payload || typeof payload !== 'object') {
    throw new Error('Invalid watchAsset params: expected payload object');
  }

  const candidate = payload as Record<string, unknown>;
  const type = typeof candidate.type === 'string' && candidate.type.trim().length > 0
    ? candidate.type.trim().toUpperCase()
    : 'ANM20';
  const source = (candidate.options && typeof candidate.options === 'object')
    ? candidate.options as Record<string, unknown>
    : candidate;

  const address = typeof source.address === 'string' ? source.address.trim() : '';
  const symbol = typeof source.symbol === 'string' ? source.symbol.trim().toUpperCase() : '';
  const decimalsRaw = source.decimals;
  const decimals = typeof decimalsRaw === 'number'
    ? Math.trunc(decimalsRaw)
    : typeof decimalsRaw === 'string' && /^\d+$/.test(decimalsRaw.trim())
      ? Number(decimalsRaw.trim())
      : NaN;
  const name = typeof source.name === 'string' && source.name.trim().length > 0 ? source.name.trim() : undefined;
  const image = typeof source.image === 'string' && source.image.trim().length > 0 ? source.image.trim() : undefined;

  if (!address || address.length > 256) throw new Error('Invalid token address');
  if (!symbol || symbol.length > 16) throw new Error('Invalid token symbol');
  if (!Number.isInteger(decimals) || decimals < 0 || decimals > 30) throw new Error('Invalid token decimals');

  return { type, address, symbol, decimals, name, image };
}

function ensureVaultDefaults(vaultData: VaultData): VaultData {
  if (!vaultData.watchedTokens) vaultData.watchedTokens = [];
  return vaultData;
}
// Initialize on install
chrome.runtime.onInstalled.addListener(() => {
  console.log('Animica Wallet installed');
});

initializeRuntimeRpc()
  .then(async () => {
    try {
      const vaultData = getUnlockedVault();
      const network = vaultData ? vaultData.networkConfigs[vaultData.currentNetwork] : null;
      const rpcUrl = await getRpcUrl(network?.rpcUrls?.[0]);
      await warmPolicyCapabilities(rpcUrl, network?.chainId);
    } catch (error) {
      console.warn('Initial policy capability warmup failed:', error);
    }
  })
  .catch((error) => {
    console.error('Failed to initialize RPC configuration:', error);
  });

chrome.storage.onChanged.addListener((changes, areaName) => {
  if (areaName !== 'local') return;
  if (!changes.rpc_url_override) return;

  const nextValue = changes.rpc_url_override.newValue;
  if (typeof nextValue === 'string' && nextValue.length > 0) {
    recreateRpcClient(nextValue);
    const vaultData = getUnlockedVault();
    const network = vaultData ? vaultData.networkConfigs[vaultData.currentNetwork] : null;
    warmPolicyCapabilities(nextValue, network?.chainId).catch((error) => console.warn('Policy capability refresh failed:', error));
  } else {
    const vaultData = getUnlockedVault();
    const currentNetwork = vaultData ? vaultData.networkConfigs[vaultData.currentNetwork] : null;
    const fallbackRpc = currentNetwork?.rpcUrls?.[0];
    const effective = getEffectiveRpcUrl(fallbackRpc);
    recreateRpcClient(effective);
    warmPolicyCapabilities(effective, currentNetwork?.chainId).catch((error) => console.warn('Policy capability refresh failed:', error));
  }
});


function normalizeErrorForUi(error: unknown): { message: string; [key: string]: unknown } {
  if (error && typeof error === 'object') {
    const obj = error as Record<string, unknown>;
    const message = typeof obj.message === 'string' ? obj.message : 'Request failed';
    return { ...obj, message };
  }
  if (error instanceof Error) return { message: error.message };
  return { message: String(error) };
}

// Message handler
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  handleMessage(message, sender).then(sendResponse).catch(error => {
    sendResponse({ error: normalizeErrorForUi(error) });
  });
  return true; // Keep channel open for async response
});

function resolveSenderOrigin(sender: chrome.runtime.MessageSender): string {
  if (typeof sender.origin === 'string' && sender.origin.length > 0) return sender.origin;
  if (typeof sender.url === 'string' && sender.url.length > 0) {
    try {
      return new URL(sender.url).origin;
    } catch {
      return sender.url;
    }
  }
  return 'unknown';
}

async function handleMessage(message: any, sender: chrome.runtime.MessageSender): Promise<any> {
  const { method, params } = message;
  const origin = resolveSenderOrigin(sender);

  switch (method) {
    case 'wallet_unlock':
      return handleUnlock(params.password);
    
    case 'wallet_lock':
      return handleLock();
    
    case 'wallet_isLocked':
      return { isLocked: !isVaultUnlocked() };
    
    case 'wallet_create':
      return handleCreate(params.password);

    case 'wallet_importWalletsJson':
      return handleImportWalletsJson(params.json);

    case 'wallet_exportWalletsJson':
      return handleExportWalletsJson(!!params?.includeSecrets);
    
    case 'wallet_hasVault':
      return { hasVault: !!(await loadVault()) };
    
    case 'wallet_getAccounts':
    case 'WALLET_LIST':
      return handleGetAccounts();

    case 'WALLET_GET_ACTIVE':
      return handleGetActiveWallet();

    case 'WALLET_SET_ACTIVE':
      return handleSetActiveWallet(params.walletId);
    
    case 'wallet_createAccount':
      return handleCreateAccount(params.label);
    
    case 'wallet_getCurrentNetwork':
      return handleGetCurrentNetwork();
    
    case 'wallet_switchNetwork':
      return handleSwitchNetwork(params);
    
    case 'wallet_getBalance':
    case 'BALANCE_GET':
      return handleGetBalance(params.address);
    
    case 'wallet_sendTransaction':
      return handleSendTransaction(params);

    case 'wallet_getRpcConfig':
      return handleGetRpcConfig();

    case 'wallet_setRpcUrl':
      return handleSetRpcUrl(params.url);

    case 'wallet_resetRpcUrl':
      return handleResetRpcUrl();

    case 'wallet_testRpcConnection':
      return handleTestRpcConnection(params.url);
    
    case 'wallet_getPendingTxs':
      return handleGetPendingTxs();

    case 'wallet_getTokens':
    case 'wallet_getWatchedAssets':
      return handleGetWatchedTokens();

    case 'wallet_getDebugState':
      return handleGetDebugState();
    
    // Provider API
    case 'eth_requestAccounts':
    case 'animica_requestAccounts':
    case 'provider_requestAccounts':
      return handleRequestAccounts(origin);
    
    case 'eth_accounts':
    case 'animica_accounts':
    case 'provider_getAccounts':
      return handleProviderGetAccounts(origin);
    
    case 'eth_chainId':
      return handleProviderGetChainIdHex();

    case 'animica_chainId':
      return handleProviderGetChainId();

    case 'provider_getChainId':
      return handleProviderGetChainId();

    case 'wallet_switchEthereumChain':
    case 'animica_switchChain':
      await handleSwitchNetwork(params);
      return null;

    case 'wallet_watchAsset':
    case 'animica_watchAsset':
    case 'animica_addToken':
      return handleWatchAsset(origin, params);

    case 'eth_sendTransaction':
    case 'animica_sendTransaction':
    case 'provider_sendTransaction':
      return handleProviderSendTransaction(origin, params);
    
    default:
      throw new Error(`Unknown method: ${method}`);
  }
}

async function handleUnlock(password: string): Promise<{ success: boolean }> {
  const vault = await loadVault();
  if (!vault) {
    throw new Error('No vault found');
  }

  try {
    const decrypted = await decrypt(vault.salt, vault.iv, vault.ciphertext, password);
    const vaultData = ensureVaultDefaults(JSON.parse(decrypted) as VaultData);
    
    setUnlockedVault(vaultData, vaultData.settings.autoLockMinutes);
    await ensureActiveWallet(vaultData);
    unlockedPassword = password;
    
    await saveState({
      isLocked: false,
      lastUnlockAt: Date.now(),
    });
    
    return { success: true };
  } catch (error) {
    throw new Error('Incorrect password');
  }
}

async function handleLock(): Promise<{ success: boolean }> {
  lockVault();
  unlockedPassword = null;
  
  await saveState({
    isLocked: true,
  });
  
  return { success: true };
}

async function handleCreate(password: string): Promise<{ success: boolean }> {
  // Create first account
  const firstAccount = createAccount('Account 1');
  
  const vaultData: VaultData = {
    accounts: [firstAccount],
    permissions: {},
    networkConfigs: NETWORKS,
    currentNetwork: 'mainnet',
    currentAccount: firstAccount.address,
    txCache: {},
    watchedTokens: [],
    settings: {
      autoLockMinutes: 5,
      showTestNetworks: true,
      defaultGasPrice: 1000000,
      defaultGasLimit: 21000,
    },
  };
  
  const json = JSON.stringify(vaultData);
  const encrypted = await encrypt(json, password);
  
  await saveVault({
    version: 1,
    ...encrypted,
  });
  
  setUnlockedVault(vaultData, vaultData.settings.autoLockMinutes);
  unlockedPassword = password;
  await saveActiveWalletId(firstAccount.address);
  
  await saveState({
    isLocked: false,
    lastUnlockAt: Date.now(),
  });
  
  return { success: true };
}

async function handleImportWalletsJson(json: string): Promise<any> {
  const vaultData = getUnlockedVault();
  if (!vaultData) {
    throw new Error('Wallet is locked');
  }

  const currentNetwork = vaultData.networkConfigs[vaultData.currentNetwork];
  const { accounts, summary } = await importWalletsJson(json, vaultData.accounts, { network: currentNetwork });

  vaultData.accounts = accounts;
  await ensureActiveWallet(vaultData);

  await saveVaultData(vaultData);

  return summary;
}

async function handleExportWalletsJson(includeSecrets: boolean): Promise<{ json: string }> {
  const vaultData = getUnlockedVault();
  if (!vaultData) {
    throw new Error('Wallet is locked');
  }

  return {
    json: exportWalletsJson(vaultData.accounts, includeSecrets),
  };
}

async function handleGetAccounts(): Promise<Account[]> {
  const vaultData = getUnlockedVault();
  if (!vaultData) {
    throw new Error('Wallet is locked');
  }

  await ensureActiveWallet(vaultData);
  
  return vaultData.accounts;
}

async function handleCreateAccount(label: string): Promise<Account> {
  const vaultData = getUnlockedVault();
  if (!vaultData) {
    throw new Error('Wallet is locked');
  }
  
  const account = createAccount(label);
  vaultData.accounts.push(account);

  const activeWalletId = await loadActiveWalletId();
  if (!activeWalletId) {
    vaultData.currentAccount = account.address;
    await saveActiveWalletId(account.address);
  }
  
  await saveVaultData(vaultData);
  
  return account;
}

async function handleGetCurrentNetwork(): Promise<any> {
  const vaultData = getUnlockedVault();
  if (!vaultData) {
    throw new Error('Wallet is locked');
  }
  
  const network = vaultData.networkConfigs[vaultData.currentNetwork];
  const effectiveRpcUrl = await getRpcUrl(network.rpcUrls?.[0]);

  let rpcChainId: number | null = null;
  let rpcWarning: string | null = null;
  try {
    const client = await getRpcClient(network.rpcUrls?.[0]);
    rpcChainId = await client.getChainId();
    if (rpcChainId !== network.chainId) {
      rpcWarning = 'RPC chain_id mismatch; switch network';
    }
  } catch {
    // Ignore RPC failures while loading network metadata.
  }

  return {
    ...network,
    effectiveRpcUrl,
    rpcChainId,
    rpcWarning,
  };
}

async function handleSwitchNetwork(networkTarget: unknown): Promise<{ success: boolean; networkId: string; chainId: number }> {
  const vaultData = getUnlockedVault();
  if (!vaultData) {
    throw new Error('Wallet is locked');
  }

  const networkId = resolveNetworkId(networkTarget, vaultData.networkConfigs);
  vaultData.currentNetwork = networkId;
  await saveVaultData(vaultData);

  const network = vaultData.networkConfigs[networkId];
  const rpcUrl = await getRpcUrl(network.rpcUrls?.[0]);
  recreateRpcClient(rpcUrl);
  warmPolicyCapabilities(rpcUrl, network.chainId).catch((error) => console.warn('Policy capability refresh failed:', error));
  
  return { success: true, networkId, chainId: network.chainId };
}

async function handleGetBalance(address: string): Promise<{ confirmed: string; available: string }> {
  // Short changelog: honor requested address, add per-wallet request cancellation,
  // and emit structured start/end/error logs with correlation IDs.
  const vaultData = getUnlockedVault();
  if (!vaultData) {
    throw new Error('Wallet is locked');
  }

  const activeWallet = await resolveActiveWallet(vaultData);
  const targetAddress = (address || activeWallet.address || '').trim();
  const normalizedAddress = targetAddress.toLowerCase();
  const network = vaultData.networkConfigs[vaultData.currentNetwork];
  const rpcUrl = await getRpcUrl(network.rpcUrls?.[0]);
  const requestId = `bal-${Date.now()}-${balanceRequestSeq++}`;
  const balanceKey = `${network.chainId}:${rpcUrl}:${normalizedAddress}`;
  const startedAt = Date.now();

  const existingController = balanceAbortByKey.get(balanceKey);
  if (existingController) {
    existingController.abort('Superseded by newer request');
  }
  const controller = new AbortController();
  balanceAbortByKey.set(balanceKey, controller);

  console.debug('[wallet-bg] balance.fetch.start', {
    requestId,
    key: balanceKey,
    activeWalletId: activeWallet.address,
    address: normalizedAddress,
    requestedAddress: address || null,
    rpcUrl,
    chainId: network.chainId,
  });

  try {
    const confirmed = await getBalance(normalizedAddress, {
      rpcUrl,
      chainId: network.chainId,
      signal: controller.signal,
      requestId,
    });
  
    // Calculate pending outgoing
    const txStore = TxStore.fromJSON(vaultData.txCache);
    const pendingOutgoing = txStore.getPendingOutgoing(normalizedAddress);
  
    const available = confirmed - pendingOutgoing;

    console.debug('[wallet-bg] balance.fetch.end', {
      requestId,
      key: balanceKey,
      chainId: network.chainId,
      rpcUrl,
      address: normalizedAddress,
      durationMs: Date.now() - startedAt,
    });

    return {
      confirmed: confirmed.toString(),
      available: available.toString(),
    };
  } catch (error) {
    console.error('[wallet-bg] balance.fetch.error', {
      requestId,
      key: balanceKey,
      chainId: network.chainId,
      rpcUrl,
      address: normalizedAddress,
      durationMs: Date.now() - startedAt,
      error: error instanceof Error ? error.message : String(error),
    });
    throw error;
  } finally {
    if (balanceAbortByKey.get(balanceKey) === controller) {
      balanceAbortByKey.delete(balanceKey);
    }
  }
}

async function handleSendTransaction(params: any): Promise<{ txid: string }> {
  try {
    const vaultData = getUnlockedVault();
    if (!vaultData) {
      throw new Error('Wallet is locked');
    }

    ensureVaultDefaults(vaultData);
    
    // Validate params
    if (!params.from || typeof params.from !== 'string') {
      throw new Error('Invalid from address');
    }
    if (!params.to || typeof params.to !== 'string') {
      throw new Error('Invalid to address');
    }

    const fromAddress = params.from.trim();
    const toAddress = params.to.trim();
    const amountBaseUnits = parseBaseUnitAmount(params.amount ?? params.value ?? 0);
    const txData = parseBytesData(params.data);
    if (amountBaseUnits === 0n && (!txData || txData.length === 0)) {
      throw new Error('Transaction must include a non-zero amount or non-empty data payload');
    }

    const network = vaultData.networkConfigs[vaultData.currentNetwork];
    const client = await getRpcClient(network.rpcUrls?.[0]);

    const activeWallet = await resolveActiveWallet(vaultData);
    if (activeWallet.address !== fromAddress) {
      throw new Error(`From address must match active wallet (${activeWallet.address})`);
    }

    // Find sender account
    const account = vaultData.accounts.find(a => a.address === activeWallet.address);
    if (!account) {
      throw new Error('Account not found');
    }

    const fromAddressInfo = decodeAddress(fromAddress);
    if (account.algId !== fromAddressInfo.algId) {
      throw new Error(
        `Account/address algorithm mismatch: account uses ${algLabel(account.algId)}, ` +
        `but from address encodes ${algLabel(fromAddressInfo.algId)}.`
      );
    }
    
    // Validate account has required signing material
    if (!account.secretKey || account.secretKey.length === 0) {
      throw new Error('Cannot sign: account is watch-only or missing secret key');
    }
    
    if (!account.publicKey || account.publicKey.length === 0) {
      throw new Error('Cannot sign: account missing public key');
    }
    
    // CRITICAL: Fetch chain context (includes genesis_hash, network, etc.)
    // This is REQUIRED for canonical signing
    txDebugLog('fetching-chain-context', { rpcUrl: client.getActiveUrl() });
    const context = await fetchChainContext(client.call.bind(client));
    
    txDebugLog('chain-context', {
      chain_id: context.chain_id,
      network: context.network,
      genesis_hash: '0x' + Array.from(context.genesis_hash).map(b => b.toString(16).padStart(2, '0')).join(''),
      fork_id: context.fork_id,
      domain: context.domain,
      prehash: context.prehash,
    });

    if (context.chain_id !== network.chainId) {
      throw new Error(`Chain ID mismatch between network (${network.chainId}) and RPC identity (${context.chain_id})`);
    }
    
    // Get nonce from RPC, accounting for pending mempool transactions.
    const nonce = await client.getPendingNonce(activeWallet.address);
    
    // Convert algId to schemeId for signing
    const schemeId = algIdToSchemeId(account.algId);
    if (schemeId === null) {
      throw new Error(`Unsupported algorithm ID: ${account.algId}`);
    }
    
    txDebugLog('building-tx', {
      from: fromAddress,
      to: toAddress,
      value: amountBaseUnits,
      nonce,
      algId: account.algId,
      schemeId,
    });
    
    // Build and sign transaction using canonical tx module
    const result = await buildAndSignTransaction(
      {
        from: activeWallet.address,
        to: toAddress,
        value: amountBaseUnits,
        fee: BigInt(params.gasPrice ?? params.maxFeePerGas ?? vaultData.settings.defaultGasPrice),
        gas_limit: BigInt(params.gasLimit ?? params.gas ?? vaultData.settings.defaultGasLimit),
        nonce,
        data: txData,
        memo: params.memo || '',
      },
      context,
      account.secretKey,
      account.publicKey,
      account.algId, // Pass algId, not schemeId
      sign
    );
    
    txDebugLog('tx-built', {
      txid: result.txid,
      scheme_id: result.envelope.auth.scheme_id,
      pubkey_len: result.envelope.auth.pubkey_bytes.length,
      sig_len: result.envelope.auth.signature_bytes.length,
      rawTxLength: result.rawTx.length,
    });
    
    const rpcUrl = await getRpcUrl(network.rpcUrls?.[0]);
    txDebugLog('sending-tx', {
      rpcUrl,
      paramsShape: 'matrix',
      rawTxPrefix: result.rawTx.slice(0, 66),
      rawTxSummary: summarizeRawTx(result.rawTx),
    });

    const sendResult = await sendRawTxPipeline({
      rpcUrl,
      rawTx: result.rawTx,
      chainIdExpected: network.chainId,
      fromAddress: activeWallet.address,
      timeoutMs: 20_000,
      accountSchemeId: schemeId,
      accountPubkeyType: account.publicKey.length === 32 ? 'ed25519-like/32-byte' : `pq-${account.publicKey.length}-byte`,
    });

    if ('error' in sendResult) {
      const sendError = sendResult.error;
      captureSendRpcResponse(null, safeJsonStringify(sendError), sendError.attempts);

      if (sendError.signaturePolicyError) {
        throw sendError.signaturePolicyError;
      }

      throw sendError;
    }

    captureSendRpcDebug(rpcUrl, 'tx.sendRawTransaction(matrix)', [{ rawTx: 'redacted' }]);
    captureSendRpcResponse({
      txHash: sendResult.txHash,
      confirmed: sendResult.confirmed,
      status: sendResult.status,
      correlationId: sendResult.correlationId,
    }, null, sendResult.attempts);

    const submittedTxHash = sendResult.txHash;

    txDebugLog('tx-sent', {
      localTxid: result.txid,
      submittedTxHash,
      sendResult,
      hashMismatch: submittedTxHash !== result.txid,
    });
    
    // Store in tx cache (convert to old format for now)
    const txStore = TxStore.fromJSON(vaultData.txCache);
    const pendingTx: PendingTx = {
      txid: result.txid,
      unsignedHash: result.txid, // Use txid as placeholder
      signedTx: {
        tx: result.envelope.body as any,
        sigs: [{
          alg: result.envelope.auth.scheme_id,
          pubkey: result.envelope.auth.pubkey_bytes,
          sig: result.envelope.auth.signature_bytes,
        }],
      },
      status: 'submitted' as TxStatus,
      submittedAt: Date.now(),
    };

    // Match CLI semantics: use tx hash returned by tx.sendRawTransaction.
    // Some nodes return an authoritative hash string or object payload.
    pendingTx.txid = submittedTxHash;
    pendingTx.unsignedHash = submittedTxHash;

    txStore.upsert(pendingTx);
    vaultData.txCache = txStore.toJSON();
    
    await saveVaultData(vaultData);
    
    return { txid: submittedTxHash };
  } catch (error) {
    // Log for debugging
    console.error('[wallet-bg] handleSendTransaction failed:', error);
    txDebugLog('tx-error', {
      error: error instanceof Error ? error.message : String(error),
      stack: error instanceof Error ? error.stack : undefined,
    });
    throw error;
  }
}

async function handleGetRpcConfig(): Promise<{ rpcUrl: string; warning?: string }> {
  const vaultData = getUnlockedVault();
  const network = vaultData ? vaultData.networkConfigs[vaultData.currentNetwork] : null;
  const rpcUrl = await getRpcUrl(network?.rpcUrls?.[0]);
  const validation = validateRpcUrl(rpcUrl);

  return {
    rpcUrl,
    warning: validation.warning,
  };
}

async function handleSetRpcUrl(url: string): Promise<{ success: boolean; rpcUrl: string; warning?: string }> {
  const validation = validateRpcUrl(url);
  await setRpcUrl(validation.normalizedUrl);
  recreateRpcClient(validation.normalizedUrl);
  const vaultData = getUnlockedVault();
  const network = vaultData ? vaultData.networkConfigs[vaultData.currentNetwork] : null;
  warmPolicyCapabilities(validation.normalizedUrl, network?.chainId).catch((error) => console.warn('Policy capability refresh failed:', error));

  return {
    success: true,
    rpcUrl: validation.normalizedUrl,
    warning: validation.warning,
  };
}

async function handleResetRpcUrl(): Promise<{ success: boolean; rpcUrl: string }> {
  await resetRpcUrl();
  const vaultData = getUnlockedVault();
  const network = vaultData ? vaultData.networkConfigs[vaultData.currentNetwork] : null;
  const rpcUrl = getEffectiveRpcUrl(network?.rpcUrls?.[0]);
  recreateRpcClient(rpcUrl);
  warmPolicyCapabilities(rpcUrl, network?.chainId).catch((error) => console.warn('Policy capability refresh failed:', error));

  return {
    success: true,
    rpcUrl,
  };
}

async function handleTestRpcConnection(url: string): Promise<{
  ok: boolean;
  rpcUrl: string;
  latencyMs: number;
  chainId: number | null;
  nodeId: string | null;
  error?: string;
}> {
  const validation = validateRpcUrl(url);
  const result = await rpcPing({ rpcUrl: validation.normalizedUrl, timeoutMs: 5000 });
  setLastPingDebug(result.rawResponse ?? null, result.error ?? null);

  return {
    ok: result.ok,
    rpcUrl: validation.normalizedUrl,
    latencyMs: result.latencyMs,
    chainId: result.chainId,
    nodeId: result.nodeId,
    error: result.error,
  };
}

async function handleGetDebugState(): Promise<any> {
  const vaultData = getUnlockedVault();
  if (!vaultData) {
    throw new Error('Wallet is locked');
  }

  const activeWallet = await resolveActiveWallet(vaultData);
  const network = vaultData.networkConfigs[vaultData.currentNetwork];
  const debugState = getBalanceDebugState();

  return {
    activeWallet: {
      label: activeWallet.label,
      address: activeWallet.address,
    },
    rpcUrl: await getRpcUrl(network.rpcUrls?.[0]),
    chainId: network.chainId,
    ...debugState,
    txRpcDebug: { ...txRpcDebugState },
  };
}

async function handleGetPendingTxs(): Promise<PendingTx[]> {
  const vaultData = getUnlockedVault();
  if (!vaultData) {
    throw new Error('Wallet is locked');
  }
  
  const txStore = TxStore.fromJSON(vaultData.txCache);
  return txStore.getAll();
}

async function handleGetWatchedTokens(): Promise<WatchedToken[]> {
  const vaultData = getUnlockedVault();
  if (!vaultData) {
    throw new Error('Wallet is locked');
  }

  ensureVaultDefaults(vaultData);
  const network = vaultData.networkConfigs[vaultData.currentNetwork];
  return (vaultData.watchedTokens || [])
    .filter((token) => token.chainId === network.chainId)
    .sort((a, b) => a.symbol.localeCompare(b.symbol));
}

async function handleWatchAsset(origin: string, params: unknown): Promise<boolean> {
  const vaultData = getUnlockedVault();
  if (!vaultData) {
    throw new Error('Wallet is locked');
  }

  ensureVaultDefaults(vaultData);
  const network = vaultData.networkConfigs[vaultData.currentNetwork];
  const parsed = parseWatchTokenParams(params);
  const now = Date.now();
  const normalizedAddress = parsed.address.toLowerCase();
  const tokens = vaultData.watchedTokens || [];

  const nextToken: WatchedToken = {
    type: parsed.type,
    address: parsed.address,
    symbol: parsed.symbol,
    decimals: parsed.decimals,
    chainId: network.chainId,
    name: parsed.name,
    image: parsed.image,
    addedAt: now,
    addedByOrigin: origin !== 'unknown' ? origin : undefined,
  };

  const existingIndex = tokens.findIndex((token) =>
    token.chainId === network.chainId && token.address.toLowerCase() === normalizedAddress,
  );

  if (existingIndex >= 0) {
    const existing = tokens[existingIndex];
    tokens[existingIndex] = {
      ...existing,
      ...nextToken,
      addedAt: existing.addedAt || now,
      addedByOrigin: existing.addedByOrigin || nextToken.addedByOrigin,
    };
  } else {
    tokens.push(nextToken);
  }

  vaultData.watchedTokens = tokens;
  await saveVaultData(vaultData);
  return true;
}

async function handleRequestAccounts(origin: string): Promise<string[]> {
  const vaultData = getUnlockedVault();
  if (!vaultData) {
    throw new Error('Wallet is locked');
  }
  
  const permissions = new PermissionManager(vaultData.permissions);
  
  // Check if already has permission
  const existing = permissions.getAuthorizedAccounts(origin);
  if (existing.length > 0) {
    permissions.updateLastUsed(origin);
    vaultData.permissions = permissions.toJSON();
    await saveVaultData(vaultData);
    return existing;
  }
  
  // TODO: Show approval popup
  // For now, auto-approve with current account
  const activeWallet = await resolveActiveWallet(vaultData);
  const currentAccount = activeWallet.address;
  if (!currentAccount) {
    throw new Error('No accounts available');
  }
  
  permissions.grantPermission(origin, [currentAccount]);
  vaultData.permissions = permissions.toJSON();
  await saveVaultData(vaultData);
  
  return [currentAccount];
}

async function handleProviderGetAccounts(origin: string): Promise<string[]> {
  const vaultData = getUnlockedVault();
  if (!vaultData) {
    return [];
  }
  
  const permissions = new PermissionManager(vaultData.permissions);
  return permissions.getAuthorizedAccounts(origin);
}

async function handleProviderGetChainId(): Promise<number> {
  const vaultData = getUnlockedVault();
  if (!vaultData) {
    throw new Error('Wallet is locked');
  }
  
  const network = vaultData.networkConfigs[vaultData.currentNetwork];
  return network.chainId;
}

async function handleProviderGetChainIdHex(): Promise<string> {
  const chainId = await handleProviderGetChainId();
  return toHexChainId(chainId);
}

function normalizeProviderSendTransactionParams(params: any): any {
  // EIP-1193 callers typically send params: [txObject]; normalize to a single tx object here.
  if (Array.isArray(params)) {
    if (params.length !== 1 || !params[0] || typeof params[0] !== 'object' || Array.isArray(params[0])) {
      throw new Error('Invalid provider_sendTransaction params: expected [txObject] or txObject');
    }
    return params[0];
  }

  if (!params || typeof params !== 'object') {
    throw new Error('Invalid provider_sendTransaction params: expected txObject');
  }

  return params;
}

async function handleProviderSendTransaction(origin: string, params: any): Promise<string> {
  // Check permission
  const vaultData = getUnlockedVault();
  if (!vaultData) {
    throw new Error('Wallet is locked');
  }

  const normalizedParams = normalizeProviderSendTransactionParams(params);
  const permissions = new PermissionManager(vaultData.permissions);
  const authorized = permissions.getAuthorizedAccounts(origin);

  if (!authorized.includes(normalizedParams.from)) {
    throw new Error('Not authorized');
  }

  // TODO: Show transaction approval popup
  // For now, auto-approve

  const result = await handleSendTransaction(normalizedParams);
  return result.txid;
}

async function saveVaultData(vaultData: VaultData): Promise<void> {
  if (!unlockedPassword) {
    throw new Error('Vault password unavailable; please lock and unlock again');
  }

  ensureVaultDefaults(vaultData);

  const encrypted = await encrypt(JSON.stringify(vaultData), unlockedPassword);

  await saveVault({
    version: 1,
    ...encrypted,
  });

  setUnlockedVault(vaultData, vaultData.settings.autoLockMinutes);
}

async function ensureActiveWallet(vaultData: VaultData): Promise<string | null> {
  const storedActiveWalletId = await loadActiveWalletId();
  const accountMap = new Map(vaultData.accounts.map(account => [account.address, account]));

  const resolved = [storedActiveWalletId, vaultData.currentAccount, vaultData.accounts[0]?.address]
    .find((walletId): walletId is string => typeof walletId === 'string' && accountMap.has(walletId));

  if (!resolved) {
    return null;
  }

  vaultData.currentAccount = resolved;
  await saveActiveWalletId(resolved);
  return resolved;
}

async function resolveActiveWallet(vaultData: VaultData): Promise<Account> {
  const activeWalletId = await ensureActiveWallet(vaultData);
  if (!activeWalletId) {
    throw new Error('No wallets available');
  }

  const activeWallet = vaultData.accounts.find(account => account.address === activeWalletId);
  if (!activeWallet) {
    throw new Error('Active wallet not found');
  }

  debugLog('active wallet resolved', { activeWalletId, label: activeWallet.label });
  return activeWallet;
}

async function handleGetActiveWallet(): Promise<Account | null> {
  const vaultData = getUnlockedVault();
  if (!vaultData) {
    throw new Error('Wallet is locked');
  }

  return resolveActiveWallet(vaultData);
}

async function handleSetActiveWallet(walletId: string): Promise<{ success: boolean }> {
  const vaultData = getUnlockedVault();
  if (!vaultData) {
    throw new Error('Wallet is locked');
  }

  const account = vaultData.accounts.find(item => item.address === walletId);
  if (!account) {
    throw new Error('Wallet not found');
  }

  vaultData.currentAccount = walletId;
  await saveActiveWalletId(walletId);
  await saveVaultData(vaultData);

  debugLog('active wallet updated', { walletId, label: account.label });
  try {
    await chrome.runtime.sendMessage({ method: 'WALLET_ACTIVE_CHANGED', params: { walletId } });
  } catch {
    // Ignore when no extension page is currently listening.
  }

  return { success: true };
}

async function initializeRuntimeRpc(): Promise<void> {
  const vaultData = getUnlockedVault();
  const network = vaultData ? vaultData.networkConfigs[vaultData.currentNetwork] : null;
  const rpcUrl = await getRpcUrl(network?.rpcUrls?.[0]);
  recreateRpcClient(rpcUrl);
}
