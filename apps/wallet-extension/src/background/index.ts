// Background service worker

import { loadVault, saveVault, loadState, saveState, setUnlockedVault, getUnlockedVault, lockVault, isVaultUnlocked } from '../core/storage';
import { encrypt, decrypt } from '../core/crypto/vault';
import { NetworkManager } from '../core/networks/manager';
import { PermissionManager } from '../core/permissions';
import { TxStore } from '../core/tx/store';
import { buildAndSignTransfer, encodeTxForRpc } from '../core/tx/builder';
import { createAccount } from '../core/wallets/account';
import { parseWalletsJson, exportWalletsJson, mergeAccounts, deduplicateAccounts } from '../core/wallets/import';
import { NETWORKS } from '../types/network';
import type { VaultData, VaultSettings } from '../types/vault';
import type { Account } from '../types/wallet';
import type { TxStatus, PendingTx } from '../types/tx';

let unlockedPassword: string | null = null;

// Initialize on install
chrome.runtime.onInstalled.addListener(() => {
  console.log('Animica Wallet installed');
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
      return handleGetAccounts();
    
    case 'wallet_createAccount':
      return handleCreateAccount(params.label);
    
    case 'wallet_getCurrentNetwork':
      return handleGetCurrentNetwork();
    
    case 'wallet_switchNetwork':
      return handleSwitchNetwork(params.networkId);
    
    case 'wallet_getBalance':
      return handleGetBalance(params.address);
    
    case 'wallet_sendTransaction':
      return handleSendTransaction(params);
    
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
  
  await saveState({
    isLocked: false,
    lastUnlockAt: Date.now(),
  });
  
  return { success: true };
}

async function handleImportWalletsJson(json: string): Promise<{ imported: number; total: number }> {
  const vaultData = getUnlockedVault();
  if (!vaultData) {
    throw new Error('Wallet is locked');
  }

  const currentNetwork = vaultData.networkConfigs[vaultData.currentNetwork];
  const importedAccounts = deduplicateAccounts(parseWalletsJson(json, { network: currentNetwork }));
  const mergedAccounts = mergeAccounts(vaultData.accounts, importedAccounts);

  vaultData.accounts = mergedAccounts;
  if (!vaultData.currentAccount && mergedAccounts.length > 0) {
    vaultData.currentAccount = mergedAccounts[0].address;
  }

  await saveVaultData(vaultData);

  return {
    imported: importedAccounts.length,
    total: mergedAccounts.length,
  };
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
  
  return vaultData.accounts;
}

async function handleCreateAccount(label: string): Promise<Account> {
  const vaultData = getUnlockedVault();
  if (!vaultData) {
    throw new Error('Wallet is locked');
  }
  
  const account = createAccount(label);
  vaultData.accounts.push(account);
  
  await saveVaultData(vaultData);
  
  return account;
}

async function handleGetCurrentNetwork(): Promise<any> {
  const vaultData = getUnlockedVault();
  if (!vaultData) {
    throw new Error('Wallet is locked');
  }
  
  const network = vaultData.networkConfigs[vaultData.currentNetwork];
  return network;
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
  
  const network = vaultData.networkConfigs[vaultData.currentNetwork];
  const client = new (await import('../core/rpc/client')).RpcClient(network.rpcUrls);
  
  const balanceHex = await client.getBalance(address);
  const confirmed = BigInt(balanceHex);
  
  // Calculate pending outgoing
  const txStore = TxStore.fromJSON(vaultData.txCache);
  const pendingOutgoing = txStore.getPendingOutgoing(address);
  
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
  const { RpcClient } = await import('../core/rpc/client');
  const client = new RpcClient(network.rpcUrls);
  
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
  const currentAccount = vaultData.currentAccount || vaultData.accounts[0]?.address;
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
