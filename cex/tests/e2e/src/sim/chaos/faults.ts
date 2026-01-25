/**
 * Chaos Fault Types and Injection Logic
 * 
 * Defines fault types and orchestration for chaos testing.
 */

import { DockerChaos, ContainerChaosScenarios } from './docker.js';
import { ToxiproxyClient, NetworkFaults } from './toxiproxy.js';

export interface FaultScenario {
  name: string;
  description: string;
  duration: number;
  execute: () => Promise<void>;
  cleanup: () => Promise<void>;
}

export interface ChaosConfig {
  docker?: DockerChaos;
  toxiproxy?: ToxiproxyClient;
}

/**
 * Chaos testing orchestrator
 */
export class ChaosTester {
  private docker?: DockerChaos;
  private dockerScenarios?: ContainerChaosScenarios;
  private toxiproxy?: ToxiproxyClient;
  private networkFaults?: NetworkFaults;
  
  private activeScenario?: FaultScenario;
  
  constructor(config: ChaosConfig) {
    if (config.docker) {
      this.docker = config.docker;
      this.dockerScenarios = new ContainerChaosScenarios(config.docker);
    }
    
    if (config.toxiproxy) {
      this.toxiproxy = config.toxiproxy;
      this.networkFaults = new NetworkFaults(config.toxiproxy);
    }
  }
  
  /**
   * Execute fault scenario
   */
  async runScenario(scenario: FaultScenario): Promise<void> {
    if (this.activeScenario) {
      throw new Error('Another scenario is already running');
    }
    
    this.activeScenario = scenario;
    
    console.log(`[Chaos] Starting scenario: ${scenario.name}`);
    console.log(`[Chaos] ${scenario.description}`);
    
    try {
      await scenario.execute();
      
      // Wait for fault duration
      if (scenario.duration > 0) {
        console.log(`[Chaos] Maintaining fault for ${scenario.duration}ms`);
        await new Promise(resolve => setTimeout(resolve, scenario.duration));
      }
      
      console.log(`[Chaos] Cleaning up scenario: ${scenario.name}`);
      await scenario.cleanup();
      
      console.log(`[Chaos] Scenario complete: ${scenario.name}`);
      
    } catch (error) {
      console.error(`[Chaos] Scenario failed:`, error);
      
      // Attempt cleanup
      try {
        await scenario.cleanup();
      } catch (cleanupError) {
        console.error(`[Chaos] Cleanup failed:`, cleanupError);
      }
      
      throw error;
      
    } finally {
      this.activeScenario = undefined;
    }
  }
  
  /**
   * Create scenarios
   */
  createScenarios(): FaultScenario[] {
    const scenarios: FaultScenario[] = [];
    
    // Docker scenarios
    if (this.docker && this.dockerScenarios) {
      scenarios.push(
        this.createContainerKillScenario('cex-api', 5000),
        this.createContainerPauseScenario('cex-matching', 10000),
        this.createRollingRestartScenario(['cex-api', 'cex-matching'])
      );
    }
    
    // Network scenarios
    if (this.networkFaults) {
      scenarios.push(
        this.createLatencyScenario('api-proxy', 500, 10000),
        this.createPacketLossScenario('api-proxy', 10, 15000),
        this.createPartitionScenario(['api-proxy', 'db-proxy'], 20000)
      );
    }
    
    return scenarios;
  }
  
  /**
   * Container kill scenario
   */
  private createContainerKillScenario(
    containerName: string,
    restartDelay: number
  ): FaultScenario {
    return {
      name: `kill_${containerName}`,
      description: `Kill container ${containerName} and restart after ${restartDelay}ms`,
      duration: 0,
      execute: async () => {
        if (!this.dockerScenarios) throw new Error('Docker not configured');
        await this.dockerScenarios.killAndRestart(containerName, restartDelay);
      },
      cleanup: async () => {
        // Cleanup handled in execute
      },
    };
  }
  
  /**
   * Container pause scenario
   */
  private createContainerPauseScenario(
    containerName: string,
    pauseDuration: number
  ): FaultScenario {
    return {
      name: `pause_${containerName}`,
      description: `Pause container ${containerName} for ${pauseDuration}ms`,
      duration: 0,
      execute: async () => {
        if (!this.dockerScenarios) throw new Error('Docker not configured');
        await this.dockerScenarios.pauseAndUnpause(containerName, pauseDuration);
      },
      cleanup: async () => {
        // Cleanup handled in execute
      },
    };
  }
  
  /**
   * Rolling restart scenario
   */
  private createRollingRestartScenario(
    containerNames: string[]
  ): FaultScenario {
    return {
      name: 'rolling_restart',
      description: `Rolling restart of: ${containerNames.join(', ')}`,
      duration: 0,
      execute: async () => {
        if (!this.dockerScenarios) throw new Error('Docker not configured');
        await this.dockerScenarios.rollingRestart(containerNames, 5000);
      },
      cleanup: async () => {
        // Cleanup handled in execute
      },
    };
  }
  
  /**
   * Network latency scenario
   */
  private createLatencyScenario(
    proxyName: string,
    latencyMs: number,
    duration: number
  ): FaultScenario {
    return {
      name: `latency_${proxyName}`,
      description: `Add ${latencyMs}ms latency to ${proxyName}`,
      duration,
      execute: async () => {
        if (!this.networkFaults) throw new Error('Toxiproxy not configured');
        await this.networkFaults.addLatency(proxyName, latencyMs, latencyMs * 0.1);
      },
      cleanup: async () => {
        if (this.toxiproxy) {
          await this.toxiproxy.clearToxics(proxyName);
        }
      },
    };
  }
  
  /**
   * Packet loss scenario
   */
  private createPacketLossScenario(
    proxyName: string,
    lossPercent: number,
    duration: number
  ): FaultScenario {
    return {
      name: `packet_loss_${proxyName}`,
      description: `Add ${lossPercent}% packet loss to ${proxyName}`,
      duration,
      execute: async () => {
        if (!this.networkFaults) throw new Error('Toxiproxy not configured');
        await this.networkFaults.addPacketLoss(proxyName, lossPercent);
      },
      cleanup: async () => {
        if (this.toxiproxy) {
          await this.toxiproxy.clearToxics(proxyName);
        }
      },
    };
  }
  
  /**
   * Network partition scenario
   */
  private createPartitionScenario(
    proxyNames: string[],
    duration: number
  ): FaultScenario {
    return {
      name: 'network_partition',
      description: `Partition network: ${proxyNames.join(', ')}`,
      duration,
      execute: async () => {
        if (!this.networkFaults) throw new Error('Toxiproxy not configured');
        await this.networkFaults.partition(proxyNames);
      },
      cleanup: async () => {
        if (this.networkFaults) {
          await this.networkFaults.healPartition(proxyNames);
        }
      },
    };
  }
  
  /**
   * Cleanup all faults
   */
  async cleanupAll(): Promise<void> {
    console.log(`[Chaos] Cleaning up all faults`);
    
    if (this.toxiproxy) {
      try {
        await this.toxiproxy.resetAll();
      } catch (error) {
        console.error(`[Chaos] Failed to reset toxiproxy:`, error);
      }
    }
    
    console.log(`[Chaos] Cleanup complete`);
  }
}

/**
 * Common chaos patterns
 */
export const CHAOS_PATTERNS = {
  /** Kill a service and verify recovery */
  SERVICE_CRASH: 'service_crash',
  
  /** Network partition between components */
  NETWORK_PARTITION: 'network_partition',
  
  /** High latency */
  HIGH_LATENCY: 'high_latency',
  
  /** Packet loss */
  PACKET_LOSS: 'packet_loss',
  
  /** Rolling restart */
  ROLLING_RESTART: 'rolling_restart',
  
  /** Resource exhaustion (pause container) */
  RESOURCE_EXHAUSTION: 'resource_exhaustion',
} as const;

export type ChaosPattern = typeof CHAOS_PATTERNS[keyof typeof CHAOS_PATTERNS];
