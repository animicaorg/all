/**
 * Animica Wallet Provider Types
 * Based on window.animica interface from wallet extension
 */

export interface AnimicaProvider {
  isAnimica: boolean;
  request(args: { method: string; params?: any[] }): Promise<any>;
  
  // Convenience methods
  animica_requestAccounts(): Promise<string[]>;
  animica_accounts(): Promise<string[]>;
  animica_chainId(): Promise<number>;
  animica_switchChain(chainId: number): Promise<void>;
  animica_signMessage(message: string): Promise<string>;
  animica_sendTransaction(tx: any): Promise<string>;
  
  // Event handling
  on(event: string, handler: (...args: any[]) => void): void;
  removeListener(event: string, handler: (...args: any[]) => void): void;
}

declare global {
  interface Window {
    animica?: AnimicaProvider;
  }
}

/**
 * Get the Animica provider instance
 */
export function getProvider(): AnimicaProvider | null {
  if (typeof window === "undefined") {
    return null;
  }
  return window.animica || null;
}

/**
 * Check if wallet is available
 */
export function isWalletAvailable(): boolean {
  return typeof window !== "undefined" && !!window.animica;
}

/**
 * Request account access
 */
export async function requestAccounts(): Promise<string[]> {
  const provider = getProvider();
  if (!provider) {
    throw new Error("Animica wallet not found");
  }
  return provider.animica_requestAccounts();
}

/**
 * Get current accounts
 */
export async function getAccounts(): Promise<string[]> {
  const provider = getProvider();
  if (!provider) {
    throw new Error("Animica wallet not found");
  }
  return provider.animica_accounts();
}

/**
 * Get current chain ID
 */
export async function getChainId(): Promise<number> {
  const provider = getProvider();
  if (!provider) {
    throw new Error("Animica wallet not found");
  }
  return provider.animica_chainId();
}

/**
 * Send a transaction
 */
export async function sendTransaction(tx: any): Promise<string> {
  const provider = getProvider();
  if (!provider) {
    throw new Error("Animica wallet not found");
  }
  return provider.animica_sendTransaction(tx);
}

/**
 * Sign a message
 */
export async function signMessage(message: string): Promise<string> {
  const provider = getProvider();
  if (!provider) {
    throw new Error("Animica wallet not found");
  }
  return provider.animica_signMessage(message);
}
