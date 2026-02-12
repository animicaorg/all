// Network configuration types

export interface NetworkConfig {
  id: string;
  name: string;
  chainId: number;
  addressHrp: string;
  supportedAddressVersions: number[];
  rpcUrls: string[];
  blockExplorer?: string;
  nativeCurrency: {
    name: string;
    symbol: string;
    decimals: number;
  };
}

const ENV_DEFAULT_RPC = (import.meta as any)?.env?.VITE_DEFAULT_RPC_URL;
export const DEFAULT_MAINNET_RPC_URL = ENV_DEFAULT_RPC || 'https://mainnet.animica.org/rpc';

export const NETWORKS: Record<string, NetworkConfig> = {
  mainnet: {
    id: 'mainnet',
    name: 'Mainnet',
    chainId: 1,
    addressHrp: 'anim',
    supportedAddressVersions: [1, 2],
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
    supportedAddressVersions: [1, 2],
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
    supportedAddressVersions: [1, 2],
    rpcUrls: ['http://127.0.0.1:28545/rpc'],
    nativeCurrency: {
      name: 'Animica',
      symbol: 'ANM',
      decimals: 9,
    },
  },
};
