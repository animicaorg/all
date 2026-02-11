// Network manager

import type { NetworkConfig } from '../../types/network';
import { NETWORKS } from '../../types/network';
import { RpcClient } from '../rpc/client';

export class NetworkManager {
  private networks: Map<string, NetworkConfig> = new Map();
  private clients: Map<string, RpcClient> = new Map();
  private currentNetwork: string;

  constructor(currentNetwork: string = 'mainnet') {
    // Initialize with default networks
    for (const [id, config] of Object.entries(NETWORKS)) {
      this.networks.set(id, config);
      this.clients.set(id, new RpcClient(config.rpcUrls));
    }
    
    this.currentNetwork = currentNetwork;
  }

  getCurrentNetwork(): NetworkConfig {
    const network = this.networks.get(this.currentNetwork);
    if (!network) {
      throw new Error(`Current network ${this.currentNetwork} not found`);
    }
    return network;
  }

  getCurrentClient(): RpcClient {
    const client = this.clients.get(this.currentNetwork);
    if (!client) {
      throw new Error(`Client for ${this.currentNetwork} not found`);
    }
    return client;
  }

  switchNetwork(networkId: string): void {
    if (!this.networks.has(networkId)) {
      throw new Error(`Network ${networkId} not found`);
    }
    this.currentNetwork = networkId;
  }

  addCustomNetwork(config: NetworkConfig): void {
    this.networks.set(config.id, config);
    this.clients.set(config.id, new RpcClient(config.rpcUrls));
  }

  getNetwork(id: string): NetworkConfig | undefined {
    return this.networks.get(id);
  }

  getAllNetworks(): NetworkConfig[] {
    return Array.from(this.networks.values());
  }

  toJSON(): { current: string; configs: Record<string, NetworkConfig> } {
    const configs: Record<string, NetworkConfig> = {};
    for (const [id, config] of this.networks.entries()) {
      configs[id] = config;
    }
    return {
      current: this.currentNetwork,
      configs,
    };
  }

  static fromJSON(data: { current: string; configs: Record<string, NetworkConfig> }): NetworkManager {
    const manager = new NetworkManager(data.current);
    
    // Override with saved configs
    for (const [id, config] of Object.entries(data.configs)) {
      manager.addCustomNetwork(config);
    }
    
    return manager;
  }
}
