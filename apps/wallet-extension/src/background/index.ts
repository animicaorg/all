// Background service worker

import { loadVault, saveVault, loadState, saveState, setUnlockedVault, getUnlockedVault, lockVault, isVaultUnlocked, loadActiveWalletId, saveActiveWalletId } from '../core/storage';
import { encrypt, decrypt } from '../core/crypto/vault';
import { PermissionManager } from '../core/permissions';
import { TxStore } from '../core/tx/store';
import { buildAndSignTransfer, encodeTxForRpc } from '../core/tx/builder';
import { createAccount } from '../core/wallets/account';
import { importWalletsJson, exportWalletsJson } from '../core/wallets/import';
import { NETWORKS } from '../types/network';
import { getEffectiveRpcUrl, getRpcUrl, resetRpcUrl, setRpcUrl, validateRpcUrl } from '../services/rpcConfig';
import { getRpcClient, recreateRpcClient } from '../services/rpcClientFactory';
import { getBalance } from '../services/balanceService';
import type { VaultData, VaultSettings } from '../types/vault';
import type { Account } from '../types/wallet';
import type { TxStatus, PendingTx } from '../types/tx';

let unlockedPassword: string | null = null;
const DEBUG_WALLET = false;

function debugLog(message: string, details?: unknown): void {
  if (!DEBUG_WALLET) return;
  console.debug(`[wallet-bg] ${message}`, details);
}

// Initialize on install
chrome.runtime.onInstalled.addListener(() => {
  console.log('Animica Wallet installed');
});

initializeRuntimeRpc().catch((error) => {
  console.error('Failed to initialize RPC configuration:', error);
});

chrome.storage.onChanged.addListener((changes, areaName) => {
  if (areaName !== 'local') return;
  if (!changes.rpc_url_override) return;

  const nextValue = changes.rpc_url_override.newValue;
  if (typeof nextValue === 'string' && nextValue.length > 0) {
    recreateRpcClient(nextValue);
  } else {
    recreateRpcClient(getEffectiveRpcUrl());
  }
});

// Message handler
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  handleMessage(message, sender).then(sendResponse).catch(error => {
    sendResponse({ error: error.message });
  });
  return true; // Keep channel open for async response
});

async function handleMessage(message: any, sender: chrome.runtime.MessageSender): Promise<any> {
  const { method, params } = message;

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
      return handleSwitchNetwork(params.networkId);
    
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
    
    // Provider API
    case 'provider_requestAccounts':
      return handleRequestAccounts(sender.origin!);
    
    case 'provider_getAccounts':
      return handleProviderGetAccounts(sender.origin!);
    
    case 'provider_getChainId':
      return handleProviderGetChainId();
    
    case 'provider_sendTransaction':
      return handleProviderSendTransaction(sender.origin!, params);
    
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
    const vaultData: VaultData = JSON.parse(decrypted);
    
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
  const effectiveRpcUrl = await getRpcUrl();

  let rpcChainId: number | null = null;
  let rpcWarning: string | null = null;
  try {
    const client = await getRpcClient();
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

async function handleSwitchNetwork(networkId: string): Promise<{ success: boolean }> {
  const vaultData = getUnlockedVault();
  if (!vaultData) {
    throw new Error('Wallet is locked');
  }
  
  if (!vaultData.networkConfigs[networkId]) {
    throw new Error(`Network ${networkId} not found`);
  }
  
  vaultData.currentNetwork = networkId;
  await saveVaultData(vaultData);
  
  return { success: true };
}

async function handleGetBalance(address: string): Promise<{ confirmed: string; available: string }> {
  const vaultData = getUnlockedVault();
  if (!vaultData) {
    throw new Error('Wallet is locked');
  }

  const activeWallet = await resolveActiveWallet(vaultData);
  const targetAddress = address || activeWallet.address;
  const network = vaultData.networkConfigs[vaultData.currentNetwork];
  const rpcUrl = await getRpcUrl();

  debugLog('fetch balance', {
    activeWalletId: activeWallet.address,
    address: targetAddress,
    rpcUrl,
    chainId: network.chainId,
  });

  const confirmed = await getBalance(targetAddress, { rpcUrl, chainId: network.chainId });
  
  // Calculate pending outgoing
  const txStore = TxStore.fromJSON(vaultData.txCache);
  const pendingOutgoing = txStore.getPendingOutgoing(targetAddress);
  
  const available = confirmed - pendingOutgoing;
  
  return {
    confirmed: confirmed.toString(),
    available: available > 0n ? available.toString() : '0',
  };
}

async function handleSendTransaction(params: any): Promise<{ txid: string }> {
  const vaultData = getUnlockedVault();
  if (!vaultData) {
    throw new Error('Wallet is locked');
  }
  
  const network = vaultData.networkConfigs[vaultData.currentNetwork];
  const client = await getRpcClient();
  
  // Get current block height for validAfter/validUntil
  const head = await client.getHead();
  const currentHeight = head.height || 0;
  
  // Find sender account
  const account = vaultData.accounts.find(a => a.address === params.from);
  if (!account || !account.secretKey) {
    throw new Error('Account not found or watch-only');
  }
  
  // Build and sign transaction
  const { signedTx, txid, unsignedHash } = await buildAndSignTransfer(
    {
      chainId: network.chainId,
      from: params.from,
      to: params.to,
      amount: params.amount,
      gasPrice: params.gasPrice || vaultData.settings.defaultGasPrice,
      gasLimit: params.gasLimit || vaultData.settings.defaultGasLimit,
      validAfter: currentHeight,
      validUntil: currentHeight + 120,
      data: params.data,
    },
    account.secretKey,
    account.publicKey,
    account.algId
  );
  
  // Encode and send
  const rawTx = encodeTxForRpc(signedTx);
  await client.sendRawTransaction(rawTx);
  
  // Store in tx cache
  const txStore = TxStore.fromJSON(vaultData.txCache);
  const pendingTx: PendingTx = {
    txid,
    unsignedHash,
    signedTx,
    status: 'submitted' as TxStatus,
    submittedAt: Date.now(),
  };
  txStore.upsert(pendingTx);
  vaultData.txCache = txStore.toJSON();
  
  await saveVaultData(vaultData);
  
  return { txid };
}

async function handleGetRpcConfig(): Promise<{ rpcUrl: string; warning?: string }> {
  const rpcUrl = await getRpcUrl();
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

  return {
    success: true,
    rpcUrl: validation.normalizedUrl,
    warning: validation.warning,
  };
}

async function handleResetRpcUrl(): Promise<{ success: boolean; rpcUrl: string }> {
  await resetRpcUrl();
  const rpcUrl = getEffectiveRpcUrl();
  recreateRpcClient(rpcUrl);

  return {
    success: true,
    rpcUrl,
  };
}

async function handleTestRpcConnection(url: string): Promise<{
  ok: boolean;
  rpcUrl: string;
  latencyMs: number;
  chainId?: number;
  headHeight?: number;
  error?: string;
}> {
  const validation = validateRpcUrl(url);
  const { RpcClient } = await import('../core/rpc/client');
  const client = new RpcClient([validation.normalizedUrl], { timeoutMs: 5000 });

  const start = performance.now();
  try {
    const [head, chainId] = await Promise.all([
      client.getHead(),
      client.getChainId(),
    ]);

    return {
      ok: true,
      rpcUrl: validation.normalizedUrl,
      latencyMs: Math.round(performance.now() - start),
      chainId,
      headHeight: head?.height,
    };
  } catch (error: any) {
    return {
      ok: false,
      rpcUrl: validation.normalizedUrl,
      latencyMs: Math.round(performance.now() - start),
      error: error?.message || 'Unknown RPC test error',
    };
  }
}

async function handleGetPendingTxs(): Promise<PendingTx[]> {
  const vaultData = getUnlockedVault();
  if (!vaultData) {
    throw new Error('Wallet is locked');
  }
  
  const txStore = TxStore.fromJSON(vaultData.txCache);
  return txStore.getAll();
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

async function handleProviderSendTransaction(origin: string, params: any): Promise<string> {
  // Check permission
  const vaultData = getUnlockedVault();
  if (!vaultData) {
    throw new Error('Wallet is locked');
  }
  
  const permissions = new PermissionManager(vaultData.permissions);
  const authorized = permissions.getAuthorizedAccounts(origin);
  
  if (!authorized.includes(params.from)) {
    throw new Error('Not authorized');
  }
  
  // TODO: Show transaction approval popup
  // For now, auto-approve
  
  const result = await handleSendTransaction(params);
  return result.txid;
}

async function saveVaultData(vaultData: VaultData): Promise<void> {
  if (!unlockedPassword) {
    throw new Error('Vault password unavailable; please lock and unlock again');
  }

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
  const rpcUrl = await getRpcUrl();
  recreateRpcClient(rpcUrl);
}
