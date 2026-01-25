/**
 * Docker Chaos Testing
 * 
 * Uses dockerode to inject chaos into containerized services:
 * - Kill/restart containers
 * - Pause/unpause containers
 * - Get container logs
 * - Network operations
 */

import Docker from 'dockerode';

export interface DockerChaosConfig {
  /** Docker socket path or host */
  socketPath?: string;
  host?: string;
  port?: number;
}

export interface ContainerInfo {
  id: string;
  name: string;
  image: string;
  state: string;
  status: string;
}

/**
 * Docker chaos orchestrator
 */
export class DockerChaos {
  private docker: Docker;
  
  constructor(config: DockerChaosConfig = {}) {
    this.docker = new Docker({
      socketPath: config.socketPath || '/var/run/docker.sock',
      host: config.host,
      port: config.port,
    });
  }
  
  /**
   * List all containers (including stopped)
   */
  async listContainers(filters?: { name?: string; label?: string }): Promise<ContainerInfo[]> {
    const options: any = {
      all: true,
    };
    
    if (filters) {
      options.filters = {};
      
      if (filters.name) {
        options.filters.name = [filters.name];
      }
      
      if (filters.label) {
        options.filters.label = [filters.label];
      }
    }
    
    const containers = await this.docker.listContainers(options);
    
    return containers.map(c => ({
      id: c.Id,
      name: c.Names[0]?.replace(/^\//, '') || c.Id.substring(0, 12),
      image: c.Image,
      state: c.State,
      status: c.Status,
    }));
  }
  
  /**
   * Get container by name or ID
   */
  async getContainer(nameOrId: string): Promise<Docker.Container | null> {
    const containers = await this.listContainers();
    const container = containers.find(c => 
      c.name === nameOrId || c.id === nameOrId || c.id.startsWith(nameOrId)
    );
    
    if (!container) {
      return null;
    }
    
    return this.docker.getContainer(container.id);
  }
  
  /**
   * Kill a container
   */
  async killContainer(nameOrId: string, signal = 'SIGKILL'): Promise<void> {
    console.log(`[Docker Chaos] Killing container: ${nameOrId} (${signal})`);
    
    const container = await this.getContainer(nameOrId);
    
    if (!container) {
      throw new Error(`Container not found: ${nameOrId}`);
    }
    
    await container.kill({ signal });
    console.log(`[Docker Chaos] Container killed: ${nameOrId}`);
  }
  
  /**
   * Stop a container gracefully
   */
  async stopContainer(nameOrId: string, timeout = 10): Promise<void> {
    console.log(`[Docker Chaos] Stopping container: ${nameOrId}`);
    
    const container = await this.getContainer(nameOrId);
    
    if (!container) {
      throw new Error(`Container not found: ${nameOrId}`);
    }
    
    await container.stop({ t: timeout });
    console.log(`[Docker Chaos] Container stopped: ${nameOrId}`);
  }
  
  /**
   * Start a stopped container
   */
  async startContainer(nameOrId: string): Promise<void> {
    console.log(`[Docker Chaos] Starting container: ${nameOrId}`);
    
    const container = await this.getContainer(nameOrId);
    
    if (!container) {
      throw new Error(`Container not found: ${nameOrId}`);
    }
    
    await container.start();
    console.log(`[Docker Chaos] Container started: ${nameOrId}`);
  }
  
  /**
   * Restart a container
   */
  async restartContainer(nameOrId: string, timeout = 10): Promise<void> {
    console.log(`[Docker Chaos] Restarting container: ${nameOrId}`);
    
    const container = await this.getContainer(nameOrId);
    
    if (!container) {
      throw new Error(`Container not found: ${nameOrId}`);
    }
    
    await container.restart({ t: timeout });
    console.log(`[Docker Chaos] Container restarted: ${nameOrId}`);
  }
  
  /**
   * Pause a container (freeze processes)
   */
  async pauseContainer(nameOrId: string): Promise<void> {
    console.log(`[Docker Chaos] Pausing container: ${nameOrId}`);
    
    const container = await this.getContainer(nameOrId);
    
    if (!container) {
      throw new Error(`Container not found: ${nameOrId}`);
    }
    
    await container.pause();
    console.log(`[Docker Chaos] Container paused: ${nameOrId}`);
  }
  
  /**
   * Unpause a container
   */
  async unpauseContainer(nameOrId: string): Promise<void> {
    console.log(`[Docker Chaos] Unpausing container: ${nameOrId}`);
    
    const container = await this.getContainer(nameOrId);
    
    if (!container) {
      throw new Error(`Container not found: ${nameOrId}`);
    }
    
    await container.unpause();
    console.log(`[Docker Chaos] Container unpaused: ${nameOrId}`);
  }
  
  /**
   * Get container logs
   */
  async getLogs(
    nameOrId: string,
    options: {
      stdout?: boolean;
      stderr?: boolean;
      tail?: number;
      since?: number;
    } = {}
  ): Promise<string> {
    const container = await this.getContainer(nameOrId);
    
    if (!container) {
      throw new Error(`Container not found: ${nameOrId}`);
    }
    
    const logStream = await container.logs({
      stdout: options.stdout ?? true,
      stderr: options.stderr ?? true,
      tail: options.tail || 100,
      since: options.since,
    });
    
    return logStream.toString('utf-8');
  }
  
  /**
   * Get container stats (CPU, memory, network)
   */
  async getStats(nameOrId: string): Promise<any> {
    const container = await this.getContainer(nameOrId);
    
    if (!container) {
      throw new Error(`Container not found: ${nameOrId}`);
    }
    
    const stats = await container.stats({ stream: false });
    return stats;
  }
  
  /**
   * Execute command in container
   */
  async exec(
    nameOrId: string,
    cmd: string[],
    options: {
      attachStdout?: boolean;
      attachStderr?: boolean;
    } = {}
  ): Promise<string> {
    const container = await this.getContainer(nameOrId);
    
    if (!container) {
      throw new Error(`Container not found: ${nameOrId}`);
    }
    
    const exec = await container.exec({
      Cmd: cmd,
      AttachStdout: options.attachStdout ?? true,
      AttachStderr: options.attachStderr ?? true,
    });
    
    const stream = await exec.start({ Detach: false });
    
    return new Promise((resolve, reject) => {
      let output = '';
      
      stream.on('data', (chunk: Buffer) => {
        output += chunk.toString('utf-8');
      });
      
      stream.on('end', () => resolve(output));
      stream.on('error', reject);
    });
  }
  
  /**
   * Wait for container to be healthy
   */
  async waitForHealthy(
    nameOrId: string,
    timeout = 60000
  ): Promise<boolean> {
    const startTime = Date.now();
    
    while (Date.now() - startTime < timeout) {
      const container = await this.getContainer(nameOrId);
      
      if (!container) {
        return false;
      }
      
      const info = await container.inspect();
      const health = info.State.Health?.Status;
      
      if (health === 'healthy') {
        return true;
      }
      
      if (info.State.Status !== 'running') {
        return false;
      }
      
      await new Promise(resolve => setTimeout(resolve, 1000));
    }
    
    return false;
  }
}

/**
 * Chaos scenarios for container orchestration
 */
export class ContainerChaosScenarios {
  private chaos: DockerChaos;
  
  constructor(chaos: DockerChaos) {
    this.chaos = chaos;
  }
  
  /**
   * Kill and restart scenario
   */
  async killAndRestart(
    containerName: string,
    restartDelay = 5000
  ): Promise<void> {
    console.log(`[Chaos Scenario] Kill and restart: ${containerName}`);
    
    await this.chaos.killContainer(containerName);
    
    console.log(`[Chaos Scenario] Waiting ${restartDelay}ms before restart`);
    await new Promise(resolve => setTimeout(resolve, restartDelay));
    
    await this.chaos.startContainer(containerName);
    
    console.log(`[Chaos Scenario] Waiting for container to be healthy`);
    const healthy = await this.chaos.waitForHealthy(containerName);
    
    if (!healthy) {
      throw new Error(`Container failed to become healthy: ${containerName}`);
    }
    
    console.log(`[Chaos Scenario] Container recovered: ${containerName}`);
  }
  
  /**
   * Pause and unpause scenario
   */
  async pauseAndUnpause(
    containerName: string,
    pauseDuration = 10000
  ): Promise<void> {
    console.log(`[Chaos Scenario] Pause for ${pauseDuration}ms: ${containerName}`);
    
    await this.chaos.pauseContainer(containerName);
    await new Promise(resolve => setTimeout(resolve, pauseDuration));
    await this.chaos.unpauseContainer(containerName);
    
    console.log(`[Chaos Scenario] Container unpaused: ${containerName}`);
  }
  
  /**
   * Rolling restart scenario
   */
  async rollingRestart(
    containerNames: string[],
    delayBetween = 5000
  ): Promise<void> {
    console.log(`[Chaos Scenario] Rolling restart of ${containerNames.length} containers`);
    
    for (const name of containerNames) {
      await this.chaos.restartContainer(name);
      
      console.log(`[Chaos Scenario] Waiting for ${name} to be healthy`);
      await this.chaos.waitForHealthy(name);
      
      if (delayBetween > 0) {
        await new Promise(resolve => setTimeout(resolve, delayBetween));
      }
    }
    
    console.log(`[Chaos Scenario] Rolling restart complete`);
  }
}
