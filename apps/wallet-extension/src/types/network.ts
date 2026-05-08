// Network configuration types

export interface NetworkConfig {
  id: string;
  name: string;
  chainId: number;
  addressHrp: string;
  rpcUrls: string[];
  blockExplorer?: string;
  nativeCurrency: {
    name: string;
    symbol: string;
    decimals: number;
  };
}

const ENV_DEFAULT_RPC = (import.meta as any)?.env?.VITE_DEFAULT_RPC_URL;
export const DEFAULT_MAINNET_RPC_URL = ENV_DEFAULT_RPC || 'https://rpc.animica.org/rpc';

export const NETWORKS: Record<string, NetworkConfig> = {
  mainnet: {
    id: 'mainnet',
    name: 'Mainnet',
    chainId: 1,
    addressHrp: 'anim',
    rpcUrls: [
      DEFAULT_MAINNET_RPC_URL,
      'http://127.0.0.1:8545/rpc',
    ],
    nativeCurrency: {
      name: 'Animica',
      symbol: 'ANM',
      decimals: 9,
    },
  },
  testnet: {
    id: 'testnet',
    name: 'Testnet',
    chainId: 2,
    addressHrp: 'animt',
    rpcUrls: ['http://127.0.0.1:18546/rpc'],
    nativeCurrency: {
      name: 'Animica',
      symbol: 'ANM',
      decimals: 9,
    },
  },
  devnet: {
    id: 'devnet',
    name: 'Devnet',
    chainId: 1337,
    addressHrp: 'animd',
    rpcUrls: ['http://127.0.0.1:28545/rpc'],
    nativeCurrency: {
      name: 'Animica',
      symbol: 'ANM',
      decimals: 9,
    },
  },
};
