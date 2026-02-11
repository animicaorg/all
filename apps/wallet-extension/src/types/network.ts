// Network configuration types

export interface NetworkConfig {
  id: string;
  name: string;
  chainId: number;
  rpcUrls: string[];
  blockExplorer?: string;
  nativeCurrency: {
    name: string;
    symbol: string;
    decimals: number;
  };
}

export const NETWORKS: Record<string, NetworkConfig> = {
  mainnet: {
    id: 'mainnet',
    name: 'Mainnet',
    chainId: 1,
    rpcUrls: [
      'http://144.126.133.21:8545/rpc',
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
    rpcUrls: ['http://127.0.0.1:28545/rpc'],
    nativeCurrency: {
      name: 'Animica',
      symbol: 'ANM',
      decimals: 9,
    },
  },
};
