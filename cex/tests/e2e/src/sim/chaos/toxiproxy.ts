/**
 * Toxiproxy Network Fault Injection
 * 
 * Integrates with Toxiproxy to inject network faults:
 * - Latency
 * - Packet loss
 * - Bandwidth limits
 * - Connection cuts
 * - Timeouts
 */

export interface ToxiproxyConfig {
  /** Toxiproxy API endpoint */
  apiUrl: string;
}

export interface Proxy {
  name: string;
  listen: string;
  upstream: string;
  enabled: boolean;
  toxics?: Toxic[];
}

export interface Toxic {
  name: string;
  type: ToxicType;
  stream?: 'upstream' | 'downstream';
  toxicity: number; // 0-1, probability of applying toxic
  attributes: Record<string, any>;
}

export type ToxicType = 
  | 'latency'
  | 'bandwidth'
  | 'slow_close'
  | 'timeout'
  | 'slicer'
  | 'limit_data';

/**
 * Toxiproxy client
 */
export class ToxiproxyClient {
  private apiUrl: string;
  
  constructor(config: ToxiproxyConfig) {
    this.apiUrl = config.apiUrl;
  }
  
  /**
   * List all proxies
   */
  async listProxies(): Promise<Proxy[]> {
    const response = await fetch(`${this.apiUrl}/proxies`);
    
    if (!response.ok) {
      throw new Error(`Failed to list proxies: ${response.statusText}`);
    }
    
    const data = await response.json();
    return Object.values(data);
  }
  
  /**
   * Create proxy
   */
  async createProxy(proxy: {
    name: string;
    listen: string;
    upstream: string;
    enabled?: boolean;
  }): Promise<Proxy> {
    const response = await fetch(`${this.apiUrl}/proxies`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        ...proxy,
        enabled: proxy.enabled ?? true,
      }),
    });
    
    if (!response.ok) {
      const error = await response.json();
      throw new Error(`Failed to create proxy: ${JSON.stringify(error)}`);
    }
    
    return await response.json();
  }
  
  /**
   * Get proxy by name
   */
  async getProxy(name: string): Promise<Proxy> {
    const response = await fetch(`${this.apiUrl}/proxies/${name}`);
    
    if (!response.ok) {
      throw new Error(`Proxy not found: ${name}`);
    }
    
    return await response.json();
  }
  
  /**
   * Delete proxy
   */
  async deleteProxy(name: string): Promise<void> {
    const response = await fetch(`${this.apiUrl}/proxies/${name}`, {
      method: 'DELETE',
    });
    
    if (!response.ok) {
      throw new Error(`Failed to delete proxy: ${response.statusText}`);
    }
  }
  
  /**
   * Enable/disable proxy
   */
  async setProxyEnabled(name: string, enabled: boolean): Promise<Proxy> {
    const proxy = await this.getProxy(name);
    
    const response = await fetch(`${this.apiUrl}/proxies/${name}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        ...proxy,
        enabled,
      }),
    });
    
    if (!response.ok) {
      throw new Error(`Failed to update proxy: ${response.statusText}`);
    }
    
    return await response.json();
  }
  
  /**
   * Add toxic to proxy
   */
  async addToxic(proxyName: string, toxic: {
    name: string;
    type: ToxicType;
    stream?: 'upstream' | 'downstream';
    toxicity?: number;
    attributes: Record<string, any>;
  }): Promise<Toxic> {
    const response = await fetch(`${this.apiUrl}/proxies/${proxyName}/toxics`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        ...toxic,
        stream: toxic.stream || 'downstream',
        toxicity: toxic.toxicity ?? 1.0,
      }),
    });
    
    if (!response.ok) {
      const error = await response.json();
      throw new Error(`Failed to add toxic: ${JSON.stringify(error)}`);
    }
    
    return await response.json();
  }
  
  /**
   * Remove toxic
   */
  async removeToxic(proxyName: string, toxicName: string): Promise<void> {
    const response = await fetch(
      `${this.apiUrl}/proxies/${proxyName}/toxics/${toxicName}`,
      { method: 'DELETE' }
    );
    
    if (!response.ok) {
      throw new Error(`Failed to remove toxic: ${response.statusText}`);
    }
  }
  
  /**
   * Update toxic
   */
  async updateToxic(
    proxyName: string,
    toxicName: string,
    updates: Partial<Toxic>
  ): Promise<Toxic> {
    const response = await fetch(
      `${this.apiUrl}/proxies/${proxyName}/toxics/${toxicName}`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(updates),
      }
    );
    
    if (!response.ok) {
      throw new Error(`Failed to update toxic: ${response.statusText}`);
    }
    
    return await response.json();
  }
  
  /**
   * Remove all toxics from proxy
   */
  async clearToxics(proxyName: string): Promise<void> {
    const proxy = await this.getProxy(proxyName);
    
    if (proxy.toxics) {
      for (const toxic of proxy.toxics) {
        await this.removeToxic(proxyName, toxic.name);
      }
    }
  }
  
  /**
   * Reset proxy (remove all toxics, re-enable)
   */
  async resetProxy(proxyName: string): Promise<void> {
    await this.clearToxics(proxyName);
    await this.setProxyEnabled(proxyName, true);
  }
  
  /**
   * Reset all proxies
   */
  async resetAll(): Promise<void> {
    const response = await fetch(`${this.apiUrl}/reset`, {
      method: 'POST',
    });
    
    if (!response.ok) {
      throw new Error(`Failed to reset: ${response.statusText}`);
    }
  }
}

/**
 * Network fault injection helpers
 */
export class NetworkFaults {
  private client: ToxiproxyClient;
  
  constructor(client: ToxiproxyClient) {
    this.client = client;
  }
  
  /**
   * Add latency to proxy
   */
  async addLatency(
    proxyName: string,
    latencyMs: number,
    jitterMs: number = 0,
    toxicName = 'latency'
  ): Promise<Toxic> {
    console.log(`[Network Fault] Adding ${latencyMs}ms latency to ${proxyName}`);
    
    return await this.client.addToxic(proxyName, {
      name: toxicName,
      type: 'latency',
      attributes: {
        latency: latencyMs,
        jitter: jitterMs,
      },
    });
  }
  
  /**
   * Add bandwidth limit
   */
  async addBandwidthLimit(
    proxyName: string,
    rateKBps: number,
    toxicName = 'bandwidth'
  ): Promise<Toxic> {
    console.log(`[Network Fault] Limiting bandwidth to ${rateKBps} KB/s on ${proxyName}`);
    
    return await this.client.addToxic(proxyName, {
      name: toxicName,
      type: 'bandwidth',
      attributes: {
        rate: rateKBps,
      },
    });
  }
  
  /**
   * Add packet loss
   */
  async addPacketLoss(
    proxyName: string,
    lossPercent: number,
    toxicName = 'slicer'
  ): Promise<Toxic> {
    console.log(`[Network Fault] Adding ${lossPercent}% packet loss to ${proxyName}`);
    
    // Convert percent to toxicity (0-1)
    const toxicity = lossPercent / 100;
    
    return await this.client.addToxic(proxyName, {
      name: toxicName,
      type: 'slicer',
      toxicity,
      attributes: {
        average_size: 1400, // Average packet size
        size_variation: 100,
        delay: 0,
      },
    });
  }
  
  /**
   * Add connection timeout
   */
  async addTimeout(
    proxyName: string,
    timeoutMs: number,
    toxicName = 'timeout'
  ): Promise<Toxic> {
    console.log(`[Network Fault] Adding ${timeoutMs}ms timeout to ${proxyName}`);
    
    return await this.client.addToxic(proxyName, {
      name: toxicName,
      type: 'timeout',
      attributes: {
        timeout: timeoutMs,
      },
    });
  }
  
  /**
   * Cut connection (disable proxy)
   */
  async cutConnection(proxyName: string): Promise<void> {
    console.log(`[Network Fault] Cutting connection: ${proxyName}`);
    await this.client.setProxyEnabled(proxyName, false);
  }
  
  /**
   * Restore connection (enable proxy)
   */
  async restoreConnection(proxyName: string): Promise<void> {
    console.log(`[Network Fault] Restoring connection: ${proxyName}`);
    await this.client.setProxyEnabled(proxyName, true);
  }
  
  /**
   * Simulate network partition (cut multiple proxies)
   */
  async partition(proxyNames: string[]): Promise<void> {
    console.log(`[Network Fault] Creating partition: ${proxyNames.join(', ')}`);
    
    for (const name of proxyNames) {
      await this.cutConnection(name);
    }
  }
  
  /**
   * Heal network partition
   */
  async healPartition(proxyNames: string[]): Promise<void> {
    console.log(`[Network Fault] Healing partition: ${proxyNames.join(', ')}`);
    
    for (const name of proxyNames) {
      await this.restoreConnection(name);
    }
  }
}
