import type { Account } from '../types/wallet';

export interface WalletActiveChangedMessage {
  method: 'WALLET_ACTIVE_CHANGED';
  params: { walletId: string };
}

export async function listWallets(): Promise<Account[]> {
  return chrome.runtime.sendMessage({ method: 'WALLET_LIST' });
}

export async function getActiveWallet(): Promise<Account | null> {
  return chrome.runtime.sendMessage({ method: 'WALLET_GET_ACTIVE' });
}

export async function setActiveWallet(walletId: string): Promise<{ success: boolean }> {
  return chrome.runtime.sendMessage({ method: 'WALLET_SET_ACTIVE', params: { walletId } });
}

export function onActiveWalletChanged(listener: (walletId: string) => void): () => void {
  const handler = (message: WalletActiveChangedMessage) => {
    if (message?.method === 'WALLET_ACTIVE_CHANGED' && typeof message.params?.walletId === 'string') {
      listener(message.params.walletId);
    }
  };

  chrome.runtime.onMessage.addListener(handler);
  return () => chrome.runtime.onMessage.removeListener(handler);
}
