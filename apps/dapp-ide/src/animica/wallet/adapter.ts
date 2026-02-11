/**
 * Animica Wallet Provider Types and Hooks
 * Based on window.animica interface from wallet extension
 */

import { useState, useEffect, useCallback } from "react";

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

export type WalletState = {
  isAvailable: boolean;
  isConnected: boolean;
  accounts: string[];
  chainId: number | null;
  error: string | null;
};

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
    throw new Error("Animica wallet not found. Please install the wallet extension.");
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
 * Switch to a different chain
 */
export async function switchChain(chainId: number): Promise<void> {
  const provider = getProvider();
  if (!provider) {
    throw new Error("Animica wallet not found");
  }
  return provider.animica_switchChain(chainId);
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

/**
 * React hook for wallet state management
 */
export function useWallet() {
  const [state, setState] = useState<WalletState>({
    isAvailable: false,
    isConnected: false,
    accounts: [],
    chainId: null,
    error: null,
  });

  const [isConnecting, setIsConnecting] = useState(false);

  // Check wallet availability on mount
  useEffect(() => {
    const checkWallet = () => {
      const available = isWalletAvailable();
      setState((prev) => ({ ...prev, isAvailable: available }));

      if (available) {
        // Try to get accounts if already connected
        getAccounts()
          .then((accounts) => {
            if (accounts.length > 0) {
              setState((prev) => ({
                ...prev,
                isConnected: true,
                accounts,
              }));
              // Get chain ID
              getChainId()
                .then((chainId) => {
                  setState((prev) => ({ ...prev, chainId }));
                })
                .catch(console.error);
            }
          })
          .catch(() => {
            // Not connected yet, that's ok
          });
      }
    };

    checkWallet();

    // Listen for wallet installation
    if (typeof window !== "undefined") {
      window.addEventListener("animica#initialized", checkWallet);
      return () => window.removeEventListener("animica#initialized", checkWallet);
    }
  }, []);

  // Set up event listeners
  useEffect(() => {
    const provider = getProvider();
    if (!provider) return;

    const handleAccountsChanged = (accounts: string[]) => {
      console.log("Accounts changed:", accounts);
      setState((prev) => ({
        ...prev,
        accounts,
        isConnected: accounts.length > 0,
      }));
    };

    const handleChainChanged = (chainId: number) => {
      console.log("Chain changed:", chainId);
      setState((prev) => ({ ...prev, chainId }));
    };

    const handleDisconnect = () => {
      console.log("Wallet disconnected");
      setState((prev) => ({
        ...prev,
        isConnected: false,
        accounts: [],
        chainId: null,
      }));
    };

    provider.on("accountsChanged", handleAccountsChanged);
    provider.on("chainChanged", handleChainChanged);
    provider.on("disconnect", handleDisconnect);

    return () => {
      provider.removeListener("accountsChanged", handleAccountsChanged);
      provider.removeListener("chainChanged", handleChainChanged);
      provider.removeListener("disconnect", handleDisconnect);
    };
  }, [state.isAvailable]);

  // Connect function
  const connect = useCallback(async () => {
    try {
      setIsConnecting(true);
      setState((prev) => ({ ...prev, error: null }));

      const accounts = await requestAccounts();
      const chainId = await getChainId();

      setState((prev) => ({
        ...prev,
        isConnected: true,
        accounts,
        chainId,
      }));

      return { accounts, chainId };
    } catch (error) {
      const message = error instanceof Error ? error.message : "Failed to connect";
      setState((prev) => ({ ...prev, error: message }));
      throw error;
    } finally {
      setIsConnecting(false);
    }
  }, []);

  // Disconnect function (clear state)
  const disconnect = useCallback(() => {
    setState((prev) => ({
      ...prev,
      isConnected: false,
      accounts: [],
      chainId: null,
    }));
  }, []);

  // Switch chain function
  const switchNetwork = useCallback(async (chainId: number) => {
    try {
      setState((prev) => ({ ...prev, error: null }));
      await switchChain(chainId);
      setState((prev) => ({ ...prev, chainId }));
    } catch (error) {
      const message = error instanceof Error ? error.message : "Failed to switch chain";
      setState((prev) => ({ ...prev, error: message }));
      throw error;
    }
  }, []);

  // Send transaction function
  const sendTx = useCallback(async (tx: any) => {
    try {
      setState((prev) => ({ ...prev, error: null }));
      return await sendTransaction(tx);
    } catch (error) {
      const message = error instanceof Error ? error.message : "Transaction failed";
      setState((prev) => ({ ...prev, error: message }));
      throw error;
    }
  }, []);

  // Sign message function
  const sign = useCallback(async (message: string) => {
    try {
      setState((prev) => ({ ...prev, error: null }));
      return await signMessage(message);
    } catch (error) {
      const message = error instanceof Error ? error.message : "Signing failed";
      setState((prev) => ({ ...prev, error: message }));
      throw error;
    }
  }, []);

  return {
    ...state,
    isConnecting,
    connect,
    disconnect,
    switchNetwork,
    sendTransaction: sendTx,
    signMessage: sign,
  };
}
