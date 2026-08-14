// Background service worker

import { sha3_256 } from 'js-sha3';
import { loadVault, saveVault, saveState, setUnlockedVault, getUnlockedVault, lockVault, isVaultUnlocked, loadActiveWalletId, saveActiveWalletId } from '../core/storage';
import { encrypt, decrypt } from '../core/crypto/vault';
import { buildCanonicalSignBytes } from '../core/crypto/sign-domain';
import { stringifyForStorage, toJsonSafe } from '../core/rpc/safeJson';
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

// ANM Instant (L2) client — shares the L1 RPC endpoint (l2_* methods).
import { L2Client } from '../l2/client';
import type { L2Signer } from '../l2/client';

let unlockedPassword: string | null = null;
const DEBUG_WALLET = true; // Always enabled for troubleshooting
let balanceRequestSeq = 1;
const balanceAbortByKey = new Map<string, AbortController>();
let lastPopupOpenedAt = 0;

const NFT_ASSET_TYPES = new Set([
  'ANM721',
  'ANM1155',
  'ERC721',
  'ERC1155',
  'NFT',
  'COLLECTIBLE',
]);

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

/**
 * Convert any payload to a JSON-safe shape before storing inside the vault.
 * bigint → decimal string; Uint8Array → numeric-keyed object (compatible
 * with coerceBytes on read).
 */
function makePendingTxStorable<T>(value: T): T {
  return toJsonSafe(value, 'storage') as T;
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

/**
 * Coerce a number/bigint/string/hex value into a bigint, with a friendly
 * fallback. Used for fee/gas defaults so a misconfigured vault setting
 * cannot crash a send with a cryptic SyntaxError out of BigInt().
 */
function safeBigInt(value: unknown, fallback: bigint): bigint {
  if (typeof value === 'bigint') return value;
  if (typeof value === 'number' && Number.isFinite(value) && Number.isInteger(value) && value >= 0) {
    return BigInt(value);
  }
  if (typeof value === 'string') {
    const trimmed = value.trim();
    if (!trimmed) return fallback;
    try {
      if (/^0x[0-9a-f]+$/i.test(trimmed)) return BigInt(trimmed);
      if (/^\d+$/.test(trimmed)) return BigInt(trimmed);
    } catch {
      return fallback;
    }
  }
  return fallback;
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

function bytesTo0xHex(bytes: Uint8Array): string {
  return `0x${Array.from(bytes).map((byte) => byte.toString(16).padStart(2, '0')).join('')}`;
}

function isNumericByteObject(value: unknown): value is Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value) || value instanceof Uint8Array) return false;
  const keys = Object.keys(value);
  return keys.length > 0 && keys.every((key) => /^\d+$/.test(key));
}

function parseNumericByteObject(value: Record<string, unknown>): Uint8Array | undefined {
  const entries = Object.entries(value)
    .map(([index, item]) => [Number(index), item] as const)
    .sort((a, b) => a[0] - b[0]);

  if (entries.length === 0) return undefined;
  if (entries[0][0] !== 0) return undefined;

  const bytes = new Uint8Array(entries.length);
  for (let i = 0; i < entries.length; i += 1) {
    const [position, rawByte] = entries[i];
    if (position !== i) return undefined;
    if (!Number.isInteger(rawByte) || rawByte < 0 || rawByte > 255) return undefined;
    bytes[i] = rawByte as number;
  }

  return bytes;
}

function coerceBytes(value: unknown): Uint8Array | undefined {
  if (value === undefined || value === null) return undefined;
  if (value instanceof Uint8Array) return value;
  if (Array.isArray(value) && value.every((item) => Number.isInteger(item) && item >= 0 && item <= 255)) {
    return Uint8Array.from(value as number[]);
  }
  if (typeof value === 'string') {
    const trimmed = value.trim();
    if (trimmed === '' || trimmed === '0x' || trimmed === '0X') return new Uint8Array();
    if (!/^0x[0-9a-f]+$/i.test(trimmed) || trimmed.length % 2 !== 0) return undefined;
    const hex = trimmed.slice(2);
    const bytes = new Uint8Array(hex.length / 2);
    for (let i = 0; i < hex.length; i += 2) {
      bytes[i / 2] = parseInt(hex.slice(i, i + 2), 16);
    }
    return bytes;
  }
  if (isNumericByteObject(value)) {
    return parseNumericByteObject(value);
  }
  return undefined;
}

function normalizeAccountBinaryFields(account: Account): Account {
  const normalizedPublicKey = coerceBytes((account as any).publicKey);
  const normalizedSecretKey = coerceBytes((account as any).secretKey);
  return {
    ...account,
    publicKey: normalizedPublicKey || new Uint8Array(),
    secretKey: normalizedSecretKey && normalizedSecretKey.length > 0 ? normalizedSecretKey : undefined,
    watchOnly: !(normalizedSecretKey && normalizedSecretKey.length > 0),
  };
}

function normalizeClaimedOrigin(value: unknown): string | null {
  if (typeof value !== 'string') return null;
  const trimmed = value.trim();
  if (!trimmed) return null;
  try {
    return new URL(trimmed).origin;
  } catch {
    return null;
  }
}

function parseBytesData(value: unknown): Uint8Array | undefined {
  const bytes = coerceBytes(value);
  if (bytes) return bytes;
  if (value === undefined || value === null) return undefined;
  if (typeof value === 'string') {
    const trimmed = value.trim();
    if (!trimmed) return new Uint8Array();
    // Allow plain text payloads (e.g. contract-call JSON envelopes) in addition to 0x-hex.
    return new TextEncoder().encode(value);
  }
  throw new Error('Invalid data: expected byte array, text, or 0x-hex string');
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

type WatchAssetInput = {
  type: string;
  address: string;
  symbol: string;
  decimals: number;
  name?: string;
  image?: string;
  tokenId?: string;
};

function parseWatchAssetParams(input: unknown): WatchAssetInput {
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
  const tokenId = source.tokenId ?? source.token_id ?? source.id;
  const parsedTokenId = typeof tokenId === 'string' && tokenId.trim().length > 0
    ? tokenId.trim()
    : typeof tokenId === 'number' && Number.isFinite(tokenId)
      ? String(Math.trunc(tokenId))
      : undefined;

  const isNft = NFT_ASSET_TYPES.has(type);
  const symbol = typeof source.symbol === 'string' && source.symbol.trim().length > 0
    ? source.symbol.trim().toUpperCase()
    : isNft
      ? 'NFT'
      : '';
  const decimalsRaw = source.decimals;
  const decimals = isNft
    ? 0
    : typeof decimalsRaw === 'number'
      ? Math.trunc(decimalsRaw)
      : typeof decimalsRaw === 'string' && /^\d+$/.test(decimalsRaw.trim())
        ? Number(decimalsRaw.trim())
        : NaN;
  const name = typeof source.name === 'string' && source.name.trim().length > 0 ? source.name.trim() : undefined;
  const image = typeof source.image === 'string' && source.image.trim().length > 0 ? source.image.trim() : undefined;

  if (!address || address.length > 256) throw new Error('Invalid token address');
  if (!symbol || symbol.length > 24) throw new Error('Invalid token symbol');
  if (!Number.isInteger(decimals) || decimals < 0 || decimals > 30) throw new Error('Invalid token decimals');

  return { type, address, symbol, decimals, name, image, tokenId: parsedTokenId };
}

function ensureVaultDefaults(vaultData: VaultData): VaultData {
  if (!Array.isArray(vaultData.accounts)) vaultData.accounts = [];
  vaultData.accounts = vaultData.accounts.map((account) => normalizeAccountBinaryFields(account));
  if (!vaultData.permissions || typeof vaultData.permissions !== 'object') vaultData.permissions = {};
  if (!vaultData.txCache || typeof vaultData.txCache !== 'object') vaultData.txCache = {};
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

// Message handler.
//
// chrome.runtime.sendMessage requires JSON-cloneable values. Any BigInt or
// Uint8Array in a handler's return tree would otherwise blow up with
// "Do not know how to serialize a BigInt" before reaching the caller —
// which is exactly the failure mode dapps and AICF pages hit when calling
// provider methods. Route every response through toJsonSafe('rpc') so
// BigInt -> decimal string and Uint8Array -> 0x-hex consistently.
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  handleMessage(message, sender)
    .then((result) => {
      try {
        sendResponse(toJsonSafe(result, 'rpc'));
      } catch (sanitizeError) {
        sendResponse({
          error: normalizeErrorForUi(
            sanitizeError instanceof Error
              ? sanitizeError
              : new Error('Failed to serialize provider response'),
          ),
        });
      }
    })
    .catch((error) => {
      try {
        sendResponse({ error: toJsonSafe(normalizeErrorForUi(error), 'rpc') });
      } catch {
        sendResponse({ error: { message: 'Internal wallet error' } });
      }
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

function resolveRequestOrigin(sender: chrome.runtime.MessageSender, message: unknown): string {
  const senderOrigin = resolveSenderOrigin(sender);
  const claimedOrigin = normalizeClaimedOrigin((message as Record<string, unknown> | null | undefined)?.origin);

  if (!claimedOrigin) return senderOrigin;
  if (senderOrigin === 'unknown') return claimedOrigin;
  if (senderOrigin === claimedOrigin) return claimedOrigin;

  console.warn('[wallet-bg] Ignoring mismatched claimed origin', { senderOrigin, claimedOrigin });
  return senderOrigin;
}

async function maybeOpenWalletPopup(reason: string): Promise<void> {
  const now = Date.now();
  if (now - lastPopupOpenedAt < 700) return;
  lastPopupOpenedAt = now;

  try {
    const actionApi: any = chrome.action;
    if (typeof actionApi?.openPopup === 'function') {
      await actionApi.openPopup();
      return;
    }
  } catch (error) {
    debugLog('action.openPopup failed', { reason, error: error instanceof Error ? error.message : String(error) });
  }

  try {
    await chrome.windows.create({
      type: 'popup',
      url: chrome.runtime.getURL('src/ui/popup.html'),
      width: 390,
      height: 640,
      focused: true,
    });
  } catch (error) {
    console.warn('[wallet-bg] Failed to open wallet popup', {
      reason,
      error: error instanceof Error ? error.message : String(error),
    });
  }
}

async function handleMessage(message: any, sender: chrome.runtime.MessageSender): Promise<any> {
  const { method, params } = message;
  const origin = resolveRequestOrigin(sender, message);

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

    // ANM Instant (L2) — shares the L1 RPC endpoint.
    case 'wallet_l2GetStatus':
      return handleL2GetStatus();
    case 'wallet_l2GetBalance':
      return handleL2GetBalance(params?.address);
    case 'wallet_l2GetTPS':
      return handleL2GetTPS();
    case 'wallet_l2GetTransaction':
      return handleL2GetTransaction(params?.txid);
    case 'wallet_l2GetWithdrawalProof':
      return handleL2GetWithdrawalProof(params?.nullifier);
    case 'wallet_l2SendInstant':
      return handleL2SendInstant(params);
    case 'wallet_l2WithdrawToL1':
      return handleL2WithdrawToL1(params);

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

    case 'wallet_getNfts':
      return handleGetWatchedNfts();

    case 'wallet_listOwnedAssets':
      return handleListOwnedAssets(params?.address);

    case 'wallet_getDebugState':
      return handleGetDebugState();
    
    // Connect-approval popup messages (from popup → background).
    case 'wallet_connectGetPending':
      return handleConnectGetPending();
    case 'wallet_connectRespond':
      return handleConnectRespond(params);

    // Signature-approval popup messages (from popup → background).
    case 'wallet_signGetPending':
      return handleSignGetPending();
    case 'wallet_signRespond':
      return handleSignRespond(params);

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

    case 'net_version':
      return handleProviderGetNetworkVersion();

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

    case 'animica_deployContract':
    case 'provider_deployContract':
      return handleProviderDeployContract(origin, params);

    case 'provider_signMessage':
    case 'animica_signMessage':
    case 'personal_sign':
      return handleProviderSignMessage(origin, method, params);

    case 'animica_getBalance':
      return handleProviderGetBalance(params);

    case 'eth_getBalance':
      return handleProviderGetBalanceHex(params);

    case 'animica_getNonce':
      return handleProviderGetNonce(params);

    case 'animica_getPublicKey':
    case 'provider_getPublicKey':
      return handleProviderGetPublicKey(origin, params);

    default:
      throw new Error(`Unknown method: ${method}`);
  }
}

/**
 * Return the public key (and algorithm id) for an authorized account.
 *
 * Required by dapps that want to verify a signature returned from
 * `animica_signMessage` server-side: the on-chain Dilithium3 verifier is a
 * commitment over `pubkey[:32] || signBytes`, so the verifier needs the
 * pubkey bytes. The address only commits to `sha3_256(pubkey)`, which is
 * not invertible. Exposing the pubkey is safe — it's already published on
 * every transaction the account has ever sent.
 */
async function handleProviderGetPublicKey(
  origin: string,
  params: unknown
): Promise<{ address: string; algId: number; algName: string; publicKey: string }> {
  if (origin === 'unknown') {
    throw new Error('Unable to determine requesting origin');
  }
  const vaultData = getUnlockedVault();
  if (!vaultData) {
    throw new Error('Wallet is locked');
  }
  ensureVaultDefaults(vaultData);

  const permissions = new PermissionManager(vaultData.permissions);
  const authorized = permissions.getAuthorizedAccounts(origin);
  if (authorized.length === 0) {
    throw new Error('Not authorized');
  }

  const requestedAddress = normalizeAddressParam(params);
  const targetAddress = requestedAddress
    ? authorized.find((a) => a.toLowerCase() === requestedAddress.toLowerCase())
    : authorized[0];
  if (!targetAddress) {
    throw new Error('Address is not authorized for this origin');
  }

  const account = vaultData.accounts.find((a) => a.address === targetAddress);
  if (!account || !account.publicKey || account.publicKey.length === 0) {
    throw new Error('Public key not available for this account');
  }

  let hex = '0x';
  for (let i = 0; i < account.publicKey.length; i += 1) {
    hex += account.publicKey[i].toString(16).padStart(2, '0');
  }
  return {
    address: account.address,
    algId: account.algId,
    algName: account.algName || (account.algId === 0x1003 ? 'ml_dsa_65' : account.algId === 0x1002 ? 'sphincs_shake_128s' : account.algId === 0x1001 ? 'dilithium3' : 'unknown'),
    publicKey: hex,
  };
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
  
  const json = stringifyForStorage(vaultData);
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
    const client = await getRpcClient(network.rpcUrls);
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

    // Reconcile local pending state with chain state: any tx whose nonce
    // has already been consumed on chain (nonce < committed_nonce) is now
    // confirmed, so it should stop reducing the available balance.
    const txStore = TxStore.fromJSON(vaultData.txCache);
    let cacheChanged = false;
    try {
      const client = await getRpcClient(network.rpcUrls);
      const committedNonce = await client.getNonce(normalizedAddress, 'latest');
      const updated = txStore.markIncludedByCommittedNonce(normalizedAddress, committedNonce);
      if (updated > 0) cacheChanged = true;
    } catch (nonceErr) {
      // Non-fatal: stay with previous pending estimate. Surface in debug only.
      debugLog('balance.fetch.nonce_reconcile_failed', {
        address: normalizedAddress,
        error: nonceErr instanceof Error ? nonceErr.message : String(nonceErr),
      });
    }

    const pendingOutgoing = txStore.getPendingOutgoing(normalizedAddress);
    const available = confirmed - pendingOutgoing;

    if (cacheChanged) {
      vaultData.txCache = txStore.toJSON();
      try { await saveVaultData(vaultData); } catch (saveErr) {
        debugLog('balance.fetch.cache_persist_failed', {
          error: saveErr instanceof Error ? saveErr.message : String(saveErr),
        });
      }
    }

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

// Double-spend guard: serialize sends per-from-address. A second send from
// the same address while the first one is mid-broadcast is rejected so a
// dapp can't accidentally (or maliciously) submit two competing transactions
// for the same nonce.
const sendsInFlight = new Set<string>();

async function handleSendTransaction(params: any): Promise<{ txid: string; contractAddress?: string }> {
  // Pull `from` up front so the guard can check before we do any heavy work.
  const fromForGuard = typeof params?.from === 'string' ? params.from.trim() : '';
  if (fromForGuard && sendsInFlight.has(fromForGuard)) {
    throw new Error('A previous transaction from this account is still being submitted. Wait for it to settle.');
  }
  if (fromForGuard) sendsInFlight.add(fromForGuard);

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

    // Determine tx kind. Explicit kind overrides; otherwise infer from
    // params: presence of `code` + `manifest` => deploy (kind=1), data
    // present => call (kind=2), otherwise transfer (kind=0). The wallet's
    // canonical body shape selects the matching payload for each kind.
    const deployCode = parseBytesData(params.code);
    const deployManifest = parseBytesData(params.manifest);
    const hasDeployPayload = !!(deployCode && deployCode.length > 0)
      && !!(deployManifest && deployManifest.length > 0);
    const txData = parseBytesData(params.data);
    let txKind: number;
    if (typeof params.kind === 'number') {
      txKind = params.kind;
    } else if (hasDeployPayload) {
      txKind = 1;
    } else if (txData && txData.length > 0) {
      txKind = 2;
    } else {
      txKind = 0;
    }

    // Deploys have no `to` address. For transfer/call we still require it.
    if (txKind !== 1 && (!params.to || typeof params.to !== 'string')) {
      throw new Error('Invalid to address');
    }

    const fromAddress = params.from.trim();
    const toAddress = txKind === 1 ? '' : params.to.trim();
    const amountBaseUnits = parseBaseUnitAmount(params.amount ?? params.value ?? 0);
    if (txKind === 0 && amountBaseUnits === 0n && (!txData || txData.length === 0)) {
      throw new Error('Transaction must include a non-zero amount or non-empty data payload');
    }
    if (txKind === 1 && (!deployCode || deployCode.length === 0)) {
      throw new Error('Deploy tx requires code bytes');
    }
    if (txKind === 1 && (!deployManifest || deployManifest.length === 0)) {
      throw new Error('Deploy tx requires manifest bytes');
    }

    const network = vaultData.networkConfigs[vaultData.currentNetwork];
    const client = await getRpcClient(network.rpcUrls);

    const activeWallet = await resolveActiveWallet(vaultData);
    if (activeWallet.address !== fromAddress) {
      throw new Error(`From address must match active wallet (${activeWallet.address})`);
    }

    // Find sender account
    const account = vaultData.accounts.find(a => a.address === activeWallet.address);
    if (!account) {
      throw new Error('Account not found');
    }

    // Authoritative balance + pending check. Without this, the UI's stale
    // balance snapshot lets the user click Send twice in quick succession
    // and the chain's mempool admits both (no balance reservation server
    // side), so the wallet appears to allow double-spending. Re-checking
    // here against fresh on-chain balance + local pendingOutgoing closes
    // that window.
    try {
      const normalizedFrom = fromAddress.toLowerCase();
      const txStorePre = TxStore.fromJSON(vaultData.txCache);
      let committedNonceForSpend: number | null = null;
      try {
        committedNonceForSpend = await client.getNonce(normalizedFrom, 'latest');
      } catch (nonceErr) {
        debugLog('send.precheck.nonce_failed', {
          error: nonceErr instanceof Error ? nonceErr.message : String(nonceErr),
        });
      }
      if (committedNonceForSpend !== null) {
        txStorePre.markIncludedByCommittedNonce(normalizedFrom, committedNonceForSpend);
        vaultData.txCache = txStorePre.toJSON();
      }
      const confirmedBase = await getBalance(normalizedFrom, {
        rpcUrl: await getRpcUrl(network.rpcUrls?.[0]),
        chainId: network.chainId,
      });
      const pendingOut = txStorePre.getPendingOutgoing(normalizedFrom);
      const available = confirmedBase - pendingOut;
      if (amountBaseUnits > available) {
        const formatBase = (v: bigint) => {
          const div = 1_000_000_000n;
          const whole = v / div;
          const frac = (v % div).toString().padStart(9, '0').replace(/0+$/, '');
          return frac.length ? `${whole}.${frac}` : whole.toString();
        };
        throw new Error(
          `Insufficient balance: ${formatBase(amountBaseUnits)} ANM requested, ` +
          `${formatBase(available)} ANM available (${formatBase(pendingOut)} ANM reserved by pending sends).`
        );
      }
    } catch (precheckErr) {
      if (precheckErr instanceof Error && precheckErr.message.startsWith('Insufficient balance')) {
        throw precheckErr;
      }
      debugLog('send.precheck.failed', {
        error: precheckErr instanceof Error ? precheckErr.message : String(precheckErr),
      });
      // Continue: chain will catch insufficient funds with its own error.
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
    
    // Build and sign transaction using canonical tx module. We pass amounts
    // as bigint and use safeBigInt for fee/gas so dapps sending strings,
    // hex, or already-bigint values all work; numbers go through BigInt
    // without crashing on misconfigured defaults.
    // Deploys need a much larger default gas limit because payloads carry
    // contract bytecode + manifest. 21k (transfer default) underprices
    // even a tiny token deploy.
    const defaultGasLimitForKind = txKind === 1
      ? safeBigInt(vaultData.settings.defaultGasLimit, 2_000_000n)
      : safeBigInt(vaultData.settings.defaultGasLimit, 21_000n);

    const result = await buildAndSignTransaction(
      {
        from: activeWallet.address,
        to: txKind === 1 ? undefined : toAddress,
        value: amountBaseUnits,
        fee: safeBigInt(
          params.gasPrice ?? params.maxFeePerGas ?? params.fee,
          safeBigInt(vaultData.settings.defaultGasPrice, 1_000_000n),
        ),
        gas_limit: safeBigInt(
          params.gasLimit ?? params.gas,
          defaultGasLimitForKind,
        ),
        nonce,
        data: txData,
        memo: params.memo || '',
        kind: txKind,
        code: txKind === 1 ? deployCode : undefined,
        manifest: txKind === 1 ? deployManifest : undefined,
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
    
    // Store in tx cache. We must convert bigint values (value/fee/gas_limit)
    // and Uint8Array fields to JSON-safe forms BEFORE handing the body to
    // saveVaultData, otherwise JSON.stringify in saveVaultData throws
    // "Do not know how to serialize a BigInt".
    const txStore = TxStore.fromJSON(vaultData.txCache);
    const pendingTx: PendingTx = {
      txid: submittedTxHash,
      unsignedHash: submittedTxHash,
      signedTx: makePendingTxStorable({
        tx: result.envelope.body,
        sigs: [{
          alg: result.envelope.auth.scheme_id,
          pubkey: result.envelope.auth.pubkey_bytes,
          sig: result.envelope.auth.signature_bytes,
        }],
      }) as any,
      status: 'submitted' as TxStatus,
      submittedAt: Date.now(),
    };

    txStore.upsert(pendingTx);
    vaultData.txCache = txStore.toJSON();

    await saveVaultData(vaultData);

    // For deploys, deterministically derive the contract address so the
    // caller (launcher UI) gets a usable address back without waiting for
    // chain indexing. Matches core/utils/deploy_metadata.derive_python_vm_package_address:
    //   sha3_256(DOMAIN_V1 || u128be(chain_id) || sender[-32:] ||
    //            0x01 || u128be(nonce) || code_hash || manifest_hash)
    let contractAddress: string | undefined;
    if (txKind === 1 && deployCode && deployManifest) {
      try {
        const senderDigest = decodeAddress(activeWallet.address).digest;
        const codeHash = new Uint8Array(sha3_256.array(deployCode));
        const manifestHash = new Uint8Array(sha3_256.array(deployManifest));
        const domain = new TextEncoder().encode('animica/deploy/python_vm_package/v1');
        const u128be = (v: number | bigint): Uint8Array => {
          let n = typeof v === 'bigint' ? v : BigInt(v);
          if (n < 0n) n = 0n;
          const out = new Uint8Array(16);
          for (let i = 15; i >= 0; i--) {
            out[i] = Number(n & 0xffn);
            n >>= 8n;
          }
          return out;
        };
        const paddedSender = new Uint8Array(32);
        paddedSender.set(senderDigest.subarray(Math.max(0, senderDigest.length - 32)));
        const buf = new Uint8Array(
          domain.length + 16 + 32 + 1 + 16 + 32 + 32,
        );
        let off = 0;
        buf.set(domain, off); off += domain.length;
        buf.set(u128be(context.chain_id), off); off += 16;
        buf.set(paddedSender, off); off += 32;
        buf[off] = 0x01; off += 1; // tag = nonce-style
        buf.set(u128be(nonce), off); off += 16;
        buf.set(codeHash, off); off += 32;
        buf.set(manifestHash, off); off += 32;
        const derived = new Uint8Array(sha3_256.array(buf));
        contractAddress = '0x' + Array.from(derived)
          .map((b) => b.toString(16).padStart(2, '0'))
          .join('');
      } catch (deriveErr) {
        debugLog('deploy.address_derive_failed', {
          error: deriveErr instanceof Error ? deriveErr.message : String(deriveErr),
        });
      }
    }

    return contractAddress
      ? { txid: submittedTxHash, contractAddress }
      : { txid: submittedTxHash };
  } catch (error) {
    // Log for debugging
    console.error('[wallet-bg] handleSendTransaction failed:', error);
    txDebugLog('tx-error', {
      error: error instanceof Error ? error.message : String(error),
      stack: error instanceof Error ? error.stack : undefined,
    });
    throw error;
  } finally {
    if (fromForGuard) sendsInFlight.delete(fromForGuard);
  }
}

// ---------------------------------------------------------------------------
// ANM Instant (L2) handlers.
//
// L2 lives on the SAME node/RPC endpoint as L1 (l2_* methods). The signing key
// never leaves the background: sendInstant/withdrawToL1 build an L2Client over
// the shared RpcClient and a signer that closes over the active account's
// secret key + the existing ML-DSA-65 `sign` primitive. The L2 address is
// identical to the L1 address, so the same key works unchanged.
// ---------------------------------------------------------------------------

async function buildL2Client(): Promise<L2Client> {
  const vaultData = getUnlockedVault();
  if (!vaultData) {
    throw new Error('Wallet is locked');
  }
  const network = vaultData.networkConfigs[vaultData.currentNetwork];
  const client = await getRpcClient(network.rpcUrls);
  return new L2Client(client);
}

function makeL2Signer(account: Account): L2Signer {
  return {
    address: account.address,
    publicKey: account.publicKey,
    algId: account.algId,
    async signHash(hash: Uint8Array): Promise<Uint8Array> {
      if (!account.secretKey || account.secretKey.length === 0) {
        throw new Error('Cannot sign: account is watch-only or missing secret key');
      }
      // Sign the raw 64-byte L2 signingHash DIRECTLY — no re-hash. `sign`
      // treats its first argument as the message for ML-DSA-65 (alg 0x1003).
      return sign(hash, account.secretKey, account.algId, account.publicKey);
    },
  };
}

async function handleL2GetStatus(): Promise<any> {
  const l2 = await buildL2Client();
  return l2.l2Status();
}

async function handleL2GetBalance(address?: string): Promise<any> {
  const vaultData = getUnlockedVault();
  if (!vaultData) {
    throw new Error('Wallet is locked');
  }
  const activeWallet = await resolveActiveWallet(vaultData);
  const target = (typeof address === 'string' && address.trim()) ? address.trim() : activeWallet.address;
  const l2 = await buildL2Client();
  return l2.l2GetBalance(target);
}

async function handleL2GetTPS(): Promise<any> {
  const l2 = await buildL2Client();
  return l2.l2GetTPS();
}

async function handleL2GetTransaction(txid: unknown): Promise<any> {
  if (typeof txid !== 'string' || !txid.trim()) {
    throw new Error('Invalid txid');
  }
  const l2 = await buildL2Client();
  return l2.l2GetTransaction(txid.trim());
}

async function handleL2GetWithdrawalProof(nullifier: unknown): Promise<any> {
  if (typeof nullifier !== 'string' || !nullifier.trim()) {
    throw new Error('Invalid nullifier');
  }
  const l2 = await buildL2Client();
  return l2.l2GetWithdrawalProof(nullifier.trim());
}

// Serialize L2 sends per-from-address, mirroring the L1 double-spend guard.
const l2SendsInFlight = new Set<string>();

async function resolveL2SendContext(params: any): Promise<{ account: Account; to: string; amount: bigint; memo?: string }> {
  const vaultData = getUnlockedVault();
  if (!vaultData) {
    throw new Error('Wallet is locked');
  }
  ensureVaultDefaults(vaultData);

  const activeWallet = await resolveActiveWallet(vaultData);
  const from = (typeof params?.from === 'string' && params.from.trim()) ? params.from.trim() : activeWallet.address;
  if (from !== activeWallet.address) {
    throw new Error(`From address must match active wallet (${activeWallet.address})`);
  }

  const account = vaultData.accounts.find((a) => a.address === activeWallet.address);
  if (!account) {
    throw new Error('Account not found');
  }
  if (!account.secretKey || account.secretKey.length === 0) {
    throw new Error('Cannot sign: account is watch-only or missing secret key');
  }
  if (!account.publicKey || account.publicKey.length === 0) {
    throw new Error('Cannot sign: account missing public key');
  }

  const to = typeof params?.to === 'string' ? params.to.trim() : '';
  if (!to) {
    throw new Error('Invalid recipient address');
  }

  const amount = parseBaseUnitAmount(params?.amount ?? params?.value ?? 0);
  if (amount <= 0n) {
    throw new Error('Amount must be greater than zero (nanos)');
  }

  return { account, to, amount, memo: typeof params?.memo === 'string' ? params.memo : undefined };
}

async function handleL2SendInstant(params: any): Promise<{ txid: string; status: string; proven: boolean }> {
  const { account, to, amount, memo } = await resolveL2SendContext(params);

  if (l2SendsInFlight.has(account.address)) {
    throw new Error('A previous ANM Instant transfer from this account is still settling. Wait for it to complete.');
  }
  l2SendsInFlight.add(account.address);
  try {
    const l2 = await buildL2Client();
    const result = await l2.sendInstant({ to, amount, memo }, makeL2Signer(account));
    return { txid: result.txid, status: result.status, proven: result.proven };
  } finally {
    l2SendsInFlight.delete(account.address);
  }
}

async function handleL2WithdrawToL1(params: any): Promise<{ txid: string; status: string; proven: boolean; withdrawalProof: unknown }> {
  const { account, to, amount, memo } = await resolveL2SendContext(params);

  if (l2SendsInFlight.has(account.address)) {
    throw new Error('A previous ANM Instant transfer from this account is still settling. Wait for it to complete.');
  }
  l2SendsInFlight.add(account.address);
  try {
    const l2 = await buildL2Client();
    const result = await l2.withdrawToL1({ to, amount, memo }, makeL2Signer(account));
    return {
      txid: result.txid,
      status: result.status,
      proven: result.proven,
      withdrawalProof: result.withdrawalProof,
    };
  } finally {
    l2SendsInFlight.delete(account.address);
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
  const result = await rpcPing({ rpcUrl: validation.normalizedUrl, timeoutMs: 20_000 });
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
    .filter((token) => token.chainId === network.chainId && !NFT_ASSET_TYPES.has((token.type || '').toUpperCase()))
    .sort((a, b) => a.symbol.localeCompare(b.symbol));
}

async function handleGetWatchedNfts(): Promise<WatchedToken[]> {
  const vaultData = getUnlockedVault();
  if (!vaultData) {
    throw new Error('Wallet is locked');
  }

  ensureVaultDefaults(vaultData);
  const network = vaultData.networkConfigs[vaultData.currentNetwork];
  return (vaultData.watchedTokens || [])
    .filter((token) => token.chainId === network.chainId && NFT_ASSET_TYPES.has((token.type || '').toUpperCase()))
    .sort((a, b) => a.symbol.localeCompare(b.symbol));
}

async function handleWatchAsset(origin: string, params: unknown): Promise<boolean> {
  const vaultData = getUnlockedVault();
  if (!vaultData) {
    throw new Error('Wallet is locked');
  }

  ensureVaultDefaults(vaultData);
  const network = vaultData.networkConfigs[vaultData.currentNetwork];
  await maybeOpenWalletPopup('watchAsset');
  const parsed = parseWatchAssetParams(params);
  const now = Date.now();
  const tokenIdSuffix = parsed.tokenId ? `:${parsed.tokenId.toLowerCase()}` : '';
  const normalizedAddress = `${parsed.address.toLowerCase()}${tokenIdSuffix}`;
  const tokens = vaultData.watchedTokens || [];

  const nextToken: WatchedToken = {
    type: parsed.type,
    address: parsed.address,
    symbol: parsed.symbol,
    decimals: parsed.decimals,
    chainId: network.chainId,
    name: parsed.name,
    image: parsed.image,
    tokenId: parsed.tokenId,
    addedAt: now,
    addedByOrigin: origin !== 'unknown' ? origin : undefined,
  };

  const existingIndex = tokens.findIndex((token) =>
    token.chainId === network.chainId &&
    `${token.address.toLowerCase()}${token.tokenId ? `:${token.tokenId.toLowerCase()}` : ''}` === normalizedAddress,
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

// Pending connect requests waiting for the popup's Accept/Deny decision.
// Keyed by an opaque requestId we hand to the popup. The popup posts a
// `connect_respond` message; that resolves the awaited promise here.
interface PendingConnect {
  requestId: string;
  origin: string;
  createdAt: number;
  resolve: (accounts: string[]) => void;
  reject: (err: Error) => void;
}
const pendingConnects = new Map<string, PendingConnect>();

function nextConnectRequestId(): string {
  return `connect_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`;
}

// ── Per-request signature approval ────────────────────────────────────────────
// signMessage is a signing oracle over the wallet's post-quantum key: a connected dapp could
// otherwise silently obtain a signature over a marketplace login challenge (→ account takeover)
// or any other Animica-domain message. Every signMessage from a dapp origin now BLOCKS on an
// explicit Approve/Deny in the popup, showing the exact bytes. It FAILS CLOSED: if the user
// closes the popup or it times out, the request is rejected, never signed.
interface PendingSignature {
  requestId: string;
  origin: string;
  address: string;
  message: string;
  createdAt: number;
  resolve: () => void;
  reject: (err: Error) => void;
}
const pendingSignatures = new Map<string, PendingSignature>();

function nextSignRequestId(): string {
  return `sign_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`;
}

// Present a signature request to the user and resolve only if they approve. Rejects (fail-closed)
// on deny, popup-close, or a 5-minute timeout.
async function awaitSignatureApproval(origin: string, address: string, message: string): Promise<void> {
  const requestId = nextSignRequestId();
  const stored = { requestId, origin, address, message: message.slice(0, 4096), createdAt: Date.now() };
  // Session storage so the popup can read it even if it opens before the background is reachable.
  await chrome.storage.session.set({ pendingSignature: stored });
  await maybeOpenWalletPopup('signMessage');

  return new Promise<void>((resolve, reject) => {
    pendingSignatures.set(requestId, { requestId, origin, address, message, createdAt: Date.now(), resolve, reject });
    setTimeout(() => {
      const entry = pendingSignatures.get(requestId);
      if (!entry) return;
      pendingSignatures.delete(requestId);
      chrome.storage.session.remove('pendingSignature').catch(() => {});
      entry.reject(new Error('Signature request timed out'));
    }, 5 * 60 * 1000);
  });
}

async function handleSignGetPending(): Promise<any> {
  const data = await chrome.storage.session.get('pendingSignature');
  return { pending: data.pendingSignature || null };
}

async function handleSignRespond(params: { requestId: string; approved: boolean }): Promise<{ ok: boolean }> {
  const entry = pendingSignatures.get(params.requestId);
  if (!entry) {
    await chrome.storage.session.remove('pendingSignature');
    return { ok: false };
  }
  pendingSignatures.delete(params.requestId);
  await chrome.storage.session.remove('pendingSignature');
  if (params.approved) entry.resolve();
  else entry.reject(new Error('User rejected the signature request'));
  return { ok: true };
}

async function handleRequestAccounts(origin: string): Promise<string[]> {
  if (origin === 'unknown') {
    throw new Error('Unable to determine requesting origin');
  }

  const vaultData = getUnlockedVault();
  if (!vaultData) {
    throw new Error('Wallet is locked');
  }

  const permissions = new PermissionManager(vaultData.permissions);

  // Already-authorized origin → return the cached list, no popup.
  const existing = permissions.getAuthorizedAccounts(origin);
  if (existing.length > 0) {
    permissions.updateLastUsed(origin);
    vaultData.permissions = permissions.toJSON();
    await saveVaultData(vaultData);
    return existing;
  }

  // No prior permission — surface an explicit Accept/Deny popup. The dapp
  // sees its `requestAccounts` Promise pend until the user clicks.
  const requestId = nextConnectRequestId();
  const accountList = Array.isArray(vaultData.accounts) ? vaultData.accounts : [];
  const stored = {
    requestId,
    origin,
    createdAt: Date.now(),
    // Surface the user's accounts so the popup can show them as checkboxes.
    accounts: accountList.map((a) => ({
      address: a.address,
      label: a.label || a.address.slice(0, 12),
    })),
  };
  // Stored in session storage so the popup can read it even if it opens
  // before the background is reachable.
  await chrome.storage.session.set({ pendingConnect: stored });
  await maybeOpenWalletPopup('requestAccounts');

  return new Promise<string[]>((resolve, reject) => {
    pendingConnects.set(requestId, {
      requestId,
      origin,
      createdAt: Date.now(),
      resolve,
      reject,
    });
    // Auto-reject after 5 minutes of inactivity so the provider Promise
    // doesn't hang forever if the user closes the popup.
    setTimeout(() => {
      const entry = pendingConnects.get(requestId);
      if (!entry) return;
      pendingConnects.delete(requestId);
      chrome.storage.session.remove("pendingConnect").catch(() => {});
      entry.reject(new Error("Connect request timed out"));
    }, 5 * 60 * 1000);
  });
}

async function handleConnectGetPending(): Promise<any> {
  const data = await chrome.storage.session.get("pendingConnect");
  return { pending: data.pendingConnect || null };
}

async function handleConnectRespond(params: {
  requestId: string;
  approved: boolean;
  accounts?: string[];
}): Promise<{ ok: boolean }> {
  const entry = pendingConnects.get(params.requestId);
  if (!entry) {
    // Could already have timed out or been resolved.
    await chrome.storage.session.remove("pendingConnect");
    return { ok: false };
  }
  pendingConnects.delete(params.requestId);
  await chrome.storage.session.remove("pendingConnect");

  if (!params.approved) {
    entry.reject(new Error("User rejected the connection"));
    return { ok: true };
  }

  const vaultData = getUnlockedVault();
  if (!vaultData) {
    entry.reject(new Error("Wallet is locked"));
    return { ok: false };
  }

  const accountList = Array.isArray(vaultData.accounts) ? vaultData.accounts : [];
  const known = new Set(accountList.map((a) => a.address));
  const accounts = (params.accounts || []).filter((a) => known.has(a));
  if (accounts.length === 0) {
    entry.reject(new Error("No valid accounts selected"));
    return { ok: false };
  }

  const permissions = new PermissionManager(vaultData.permissions);
  permissions.grantPermission(entry.origin, accounts);
  vaultData.permissions = permissions.toJSON();
  await saveVaultData(vaultData);

  entry.resolve(accounts);
  return { ok: true };
}

async function handleProviderGetAccounts(origin: string): Promise<string[]> {
  if (origin === 'unknown') return [];

  const vaultData = getUnlockedVault();
  if (!vaultData) {
    return [];
  }
  
  const permissions = new PermissionManager(vaultData.permissions);
  const accounts = permissions.getAuthorizedAccounts(origin);
  if (accounts.length > 0) {
    permissions.updateLastUsed(origin);
    vaultData.permissions = permissions.toJSON();
    await saveVaultData(vaultData);
  }
  return accounts;
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

async function handleProviderGetNetworkVersion(): Promise<string> {
  const chainId = await handleProviderGetChainId();
  return String(chainId);
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

async function handleProviderDeployContract(
  origin: string,
  params: any,
): Promise<{ txid: string; contractAddress?: string }> {
  if (origin === 'unknown') {
    throw new Error('Unable to determine requesting origin');
  }
  const vaultData = getUnlockedVault();
  if (!vaultData) {
    throw new Error('Wallet is locked');
  }

  const payload = Array.isArray(params) ? params[0] : params;
  if (!payload || typeof payload !== 'object') {
    throw new Error('Invalid deploy params: expected { from?, code, manifest, value?, gasLimit?, gasPrice? }');
  }
  const obj = payload as Record<string, unknown>;
  if (!obj.code) throw new Error('Deploy params missing `code`');
  if (!obj.manifest) throw new Error('Deploy params missing `manifest`');

  const permissions = new PermissionManager(vaultData.permissions);
  const authorized = permissions.getAuthorizedAccounts(origin);
  const fromAddress = typeof obj.from === 'string' ? obj.from : authorized[0];
  if (typeof fromAddress !== 'string' || !fromAddress) {
    throw new Error('Not authorized');
  }
  const matchedAuthorized = authorized.find(
    (address) => address.toLowerCase() === fromAddress.toLowerCase(),
  );
  if (!matchedAuthorized) {
    throw new Error('Not authorized');
  }

  await maybeOpenWalletPopup('deployContract');
  permissions.updateLastUsed(origin);
  vaultData.permissions = permissions.toJSON();
  await saveVaultData(vaultData);

  return handleSendTransaction({
    from: matchedAuthorized,
    code: obj.code,
    manifest: obj.manifest,
    value: obj.value ?? 0,
    gasLimit: obj.gasLimit ?? obj.gas,
    gasPrice: obj.gasPrice ?? obj.maxFeePerGas ?? obj.fee,
    kind: 1,
  });
}

async function handleProviderSendTransaction(origin: string, params: any): Promise<string> {
  if (origin === 'unknown') {
    throw new Error('Unable to determine requesting origin');
  }

  // Check permission
  const vaultData = getUnlockedVault();
  if (!vaultData) {
    throw new Error('Wallet is locked');
  }

  const normalizedParams = normalizeProviderSendTransactionParams(params);
  const permissions = new PermissionManager(vaultData.permissions);
  const authorized = permissions.getAuthorizedAccounts(origin);
  const fromAddress = typeof normalizedParams.from === 'string' ? normalizedParams.from : authorized[0];
  if (typeof fromAddress !== 'string' || !fromAddress) {
    throw new Error('Not authorized');
  }
  const matchedAuthorizedAddress = authorized.find((address) => address.toLowerCase() === fromAddress.toLowerCase());
  normalizedParams.from = matchedAuthorizedAddress || fromAddress;

  if (!matchedAuthorizedAddress) {
    throw new Error('Not authorized');
  }

  await maybeOpenWalletPopup('sendTransaction');
  permissions.updateLastUsed(origin);
  vaultData.permissions = permissions.toJSON();
  await saveVaultData(vaultData);

  const result = await handleSendTransaction(normalizedParams);
  return result.txid;
}

function normalizeProviderSignMessageParams(method: string, params: unknown): { message: string; from?: string } {
  if (method === 'personal_sign') {
    if (!Array.isArray(params) || params.length < 1) {
      throw new Error('Invalid personal_sign params');
    }
    const first = params[0];
    const second = params[1];
    const looksLikeAnimicaAddress = (value: unknown): value is string =>
      typeof value === 'string' && /^anim1[02-9ac-hj-np-z]+$/i.test(value);

    if (looksLikeAnimicaAddress(first) && typeof second === 'string') {
      return { message: second, from: first };
    }
    if (typeof first === 'string') {
      return { message: first, from: looksLikeAnimicaAddress(second) ? second : undefined };
    }
    throw new Error('Invalid personal_sign params');
  }

  const payload = Array.isArray(params) ? params[0] : params;
  if (typeof payload === 'string') return { message: payload };
  if (!payload || typeof payload !== 'object') throw new Error('Invalid signMessage params');

  const candidate = payload as Record<string, unknown>;
  const message = typeof candidate.message === 'string' ? candidate.message : '';
  const from = typeof candidate.from === 'string' ? candidate.from : undefined;

  if (!message) throw new Error('Missing message');
  return { message, from };
}

async function handleProviderSignMessage(origin: string, method: string, params: unknown): Promise<string> {
  if (origin === 'unknown') {
    throw new Error('Unable to determine requesting origin');
  }

  const vaultData = getUnlockedVault();
  if (!vaultData) {
    throw new Error('Wallet is locked');
  }

  // Force account-binary normalization so publicKey / secretKey are real
  // Uint8Array regardless of how they were last persisted (numeric-keyed
  // object form from chrome.storage is otherwise a Record<string, number>
  // and breaks sign(), which is what dapps and AICF hit as
  // "invalid signature errors").
  ensureVaultDefaults(vaultData);

  const request = normalizeProviderSignMessageParams(method, params);
  const permissions = new PermissionManager(vaultData.permissions);
  const authorized = permissions.getAuthorizedAccounts(origin);
  const signerAddress = request.from || authorized[0];
  const matchedSignerAddress = signerAddress
    ? authorized.find((address) => address.toLowerCase() === signerAddress.toLowerCase())
    : undefined;
  if (!matchedSignerAddress) {
    throw new Error('Not authorized');
  }

  const account = vaultData.accounts.find((candidate) => candidate.address === matchedSignerAddress);
  if (!account) {
    throw new Error('Account not found');
  }
  if (!account.secretKey || account.secretKey.length === 0) {
    throw new Error('Cannot sign: account is watch-only or missing secret key');
  }

  // BLOCK on explicit user approval before signing. This is the guard that turns signMessage from
  // a silent oracle into a consented action: the popup shows the origin + the exact message, and we
  // only proceed if the user approves. Fails closed (throws) on deny / close / timeout.
  await awaitSignatureApproval(origin, matchedSignerAddress, request.message);

  permissions.updateLastUsed(origin);
  vaultData.permissions = permissions.toJSON();
  await saveVaultData(vaultData);

  // Canonical signing domain.
  //
  // Previously this string interpolated the RPC method name, so the same
  // `message` produced different signed bytes depending on whether the
  // dapp called `animica_signMessage`, `provider_signMessage`, or
  // `personal_sign`. AICF (and any dapp that tries the methods in fallback
  // order) would then see an "invalid signature" because the bytes the
  // signature was computed over depended on which method happened to
  // succeed first.
  //
  // The fix: pin the prefix to a method-independent constant so the same
  // logical request always produces a signature over the same bytes. The
  // method name is still carried in the RPC envelope for routing, but it
  // does not affect signed-message semantics.
  const signBytes = buildCanonicalSignBytes(request.message);
  const signature = await sign(signBytes, account.secretKey, account.algId, account.publicKey);
  return bytesTo0xHex(signature);
}

function normalizeAddressParam(params: unknown): string | undefined {
  if (Array.isArray(params) && typeof params[0] === 'string' && params[0].trim().length > 0) {
    return params[0].trim();
  }
  if (typeof params === 'string' && params.trim().length > 0) {
    return params.trim();
  }
  if (params && typeof params === 'object') {
    const address = (params as Record<string, unknown>).address;
    if (typeof address === 'string' && address.trim().length > 0) return address.trim();
  }
  return undefined;
}

async function resolveProviderAddress(params: unknown): Promise<string> {
  const requestedAddress = normalizeAddressParam(params);
  if (requestedAddress) return requestedAddress;

  const vaultData = getUnlockedVault();
  if (!vaultData) throw new Error('Wallet is locked');
  const activeWallet = await resolveActiveWallet(vaultData);
  return activeWallet.address;
}

async function handleProviderGetBalance(params: unknown): Promise<string> {
  const address = await resolveProviderAddress(params);
  const result = await handleGetBalance(address);
  return result.confirmed;
}

async function handleProviderGetBalanceHex(params: unknown): Promise<string> {
  const balance = await handleProviderGetBalance(params);
  // Defensive: balance is expected to be a decimal/0x string, but accept any
  // numeric-like value without crashing. Anything malformed becomes 0x0.
  try {
    const normalized = typeof balance === 'string' ? balance.trim() : String(balance ?? '');
    if (!normalized) return '0x0';
    return `0x${BigInt(normalized).toString(16)}`;
  } catch {
    return '0x0';
  }
}

async function handleProviderGetNonce(params: unknown): Promise<number> {
  const address = await resolveProviderAddress(params);
  const vaultData = getUnlockedVault();
  if (!vaultData) throw new Error('Wallet is locked');

  const network = vaultData.networkConfigs[vaultData.currentNetwork];
  const client = await getRpcClient(network.rpcUrls);
  return client.getPendingNonce(address);
}

async function saveVaultData(vaultData: VaultData): Promise<void> {
  if (!unlockedPassword) {
    throw new Error('Vault password unavailable; please lock and unlock again');
  }

  ensureVaultDefaults(vaultData);

  const encrypted = await encrypt(stringifyForStorage(vaultData), unlockedPassword);

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

// ---------------------------------------------------------------------------
// Collection: list NFTs + ANM-20 tokens owned by an address.
// ---------------------------------------------------------------------------
//
// Hits the marketplace API on animica.xyz (which is itself fed by the
// chain indexer) for a fast pre-aggregated view. Falls back to a
// best-effort RPC scan against any locally-watched contracts if the
// remote API is unreachable.
//
// Cached by the popup; this is just the source-of-truth fetch.

interface OwnedTokenRow {
  contractAddress: string;
  name: string;
  symbol: string;
  decimals: number;
  balance: string;
}

interface OwnedNftRow {
  contractAddress: string;
  collectionName: string;
  collectionSymbol: string;
  tokenId: string;
  name: string;
  imageUrl: string | null;
  marketplaceUrl: string;
}

interface ListOwnedAssetsResult {
  tokens: OwnedTokenRow[];
  nfts: OwnedNftRow[];
  error?: string;
}

const MARKETPLACE_API_BASE =
  process.env.PLASMO_PUBLIC_MARKETPLACE_BASE ?? 'https://animica.xyz';

async function handleListOwnedAssets(
  address: string | undefined,
): Promise<ListOwnedAssetsResult> {
  if (!address) return { tokens: [], nfts: [], error: 'no_address' };
  try {
    const res = await fetch(
      `${MARKETPLACE_API_BASE}/api/marketplace/profile/${address}`,
      { signal: AbortSignal.timeout(8_000) },
    );
    if (!res.ok) throw new Error(`api_${res.status}`);
    const j = await res.json() as {
      owned?: Array<{
        tokenId: string;
        name: string | null;
        imageUrl: string | null;
        collection: { address: string; name: string; symbol?: string };
      }>;
    };
    // The marketplace API is NFT-centric. Tokens come from the locally-
    // watched ANM-20 list (handleGetWatchedTokens) so users see the
    // same balances they see on the Send tab.
    const watched = await handleGetWatchedTokens().catch(() => [] as any[]);
    const tokens: OwnedTokenRow[] = Array.isArray(watched)
      ? watched.map((t: any) => ({
          contractAddress: String(t.contractAddress ?? t.address ?? ''),
          name: String(t.name ?? ''),
          symbol: String(t.symbol ?? ''),
          decimals: Number(t.decimals ?? 0),
          balance: String(t.balance ?? '0'),
        }))
      : [];
    const nfts: OwnedNftRow[] = (j.owned ?? []).map((n) => ({
      contractAddress: n.collection.address,
      collectionName: n.collection.name,
      collectionSymbol: n.collection.symbol ?? '',
      tokenId: n.tokenId,
      name: n.name ?? `#${n.tokenId}`,
      imageUrl: n.imageUrl,
      marketplaceUrl: `${MARKETPLACE_API_BASE}/marketplace/nft/${n.collection.address}:${n.tokenId}`,
    }));
    return { tokens, nfts };
  } catch (e) {
    return {
      tokens: [],
      nfts: [],
      error: e instanceof Error ? e.message : String(e),
    };
  }
}
