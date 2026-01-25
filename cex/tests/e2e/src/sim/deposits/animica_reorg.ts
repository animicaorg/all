/**
 * Animica Chain Reorganization Simulator
 * 
 * Simulates blockchain reorgs to test deposit safety:
 * 1. Create competing chain forks
 * 2. Deposit in original chain
 * 3. Make competing fork longer
 * 4. Verify deposit handling during reorg
 */

import { AnimicaDevnetClient } from './animica_devnet.js';

export interface ReorgConfig {
  /** RPC endpoints for multiple nodes */
  nodeUrls: string[];
  /** Faucet private key */
  faucetPrivateKey: string;
  /** Chain ID */
  chainId: number;
  /** Reorg depth (how many blocks to reorg) */
  reorgDepth: number;
}

export interface ReorgScenario {
  /** Original chain head before reorg */
  originalHead: number;
  /** Deposit transaction hash on original chain */
  originalTxHash: string;
  /** Block containing original deposit */
  originalBlock: number;
  /** New chain head after reorg */
  newHead: number;
  /** Whether deposit was included in new chain */
  depositIncluded: boolean;
  /** New transaction hash if different */
  newTxHash?: string;
}

/**
 * Multi-node orchestrator for reorg testing
 */
export class ReorgSimulator {
  private config: ReorgConfig;
  private clients: AnimicaDevnetClient[];
  
  constructor(config: ReorgConfig) {
    this.config = config;
    
    // Create client for each node
    this.clients = config.nodeUrls.map(url => 
      new AnimicaDevnetClient({
        rpcUrl: url,
        faucetPrivateKey: config.faucetPrivateKey,
        chainId: config.chainId,
        requiredConfirmations: 1,
      })
    );
  }
  
  /**
   * Execute reorg scenario
   */
  async executeReorg(params: {
    depositAddress: string;
    depositAmount: string;
    faucetAddress: string;
  }): Promise<ReorgScenario> {
    if (this.clients.length < 2) {
      throw new Error('Reorg simulation requires at least 2 nodes');
    }
    
    const [node1, node2] = this.clients;
    
    console.log(`[Reorg] Step 1: Get initial chain state`);
    const originalHead = await node1.getBlockHeight();
    
    console.log(`[Reorg] Step 2: Disconnect nodes to create split`);
    // In practice, you'd use network partitioning (docker network, toxiproxy, etc.)
    // For simulation, we assume nodes are already isolated
    
    console.log(`[Reorg] Step 3: Create deposit on node1`);
    const depositTx = await node1.sendTransaction({
      from: params.faucetAddress,
      to: params.depositAddress,
      value: params.depositAmount,
    });
    
    console.log(`[Reorg] Deposit TX on chain A: ${depositTx}`);
    
    // Wait for inclusion in a block
    const depositReceipt = await node1.waitForConfirmation(depositTx, 1);
    const originalBlock = depositReceipt.blockNumber;
    
    console.log(`[Reorg] Step 4: Mine competing chain on node2`);
    // Mine more blocks on node2 to make it the longer chain
    const blocksToMine = this.config.reorgDepth + 2;
    await this.mineBlocks(node2, blocksToMine);
    
    const node2Head = await node2.getBlockHeight();
    console.log(`[Reorg] Chain B height: ${node2Head}`);
    
    console.log(`[Reorg] Step 5: Reconnect nodes (trigger reorg)`);
    // When nodes reconnect, node1 should switch to node2's chain
    // In practice, this would be done via network ops
    await this.triggerReorg(node1, node2);
    
    console.log(`[Reorg] Step 6: Check if deposit survived reorg`);
    const newHead = await node1.getBlockHeight();
    
    let depositIncluded = false;
    let newTxHash: string | undefined;
    
    try {
      // Try to find the original transaction
      const tx = await node1.getTransaction(depositTx);
      if (tx && tx.blockNumber) {
        depositIncluded = true;
        newTxHash = depositTx;
        console.log(`[Reorg] Deposit INCLUDED in new chain at block ${tx.blockNumber}`);
      }
    } catch (error) {
      console.log(`[Reorg] Deposit NOT included in new chain`);
    }
    
    return {
      originalHead,
      originalTxHash: depositTx,
      originalBlock,
      newHead,
      depositIncluded,
      newTxHash,
    };
  }
  
  /**
   * Mine empty blocks on a node
   */
  private async mineBlocks(client: AnimicaDevnetClient, count: number): Promise<void> {
    console.log(`[Reorg] Mining ${count} blocks...`);
    
    for (let i = 0; i < count; i++) {
      // In Animica, mining might be triggered via RPC or happen automatically
      // This is a placeholder for the mining mechanism
      await this.triggerBlockProduction(client);
      
      // Small delay between blocks
      await new Promise(resolve => setTimeout(resolve, 100));
    }
  }
  
  /**
   * Trigger block production on node
   */
  private async triggerBlockProduction(client: AnimicaDevnetClient): Promise<void> {
    // Method depends on devnet setup:
    // - Could be RPC call like "animica_mineBlock"
    // - Could be automatic with time passing
    // - Could be triggered via special transaction
    
    try {
      // Attempt to call mining RPC (if available)
      await (client as any).rpc('animica_mineBlock');
    } catch (error) {
      // If mining RPC not available, just wait
      await new Promise(resolve => setTimeout(resolve, 1000));
    }
  }
  
  /**
   * Trigger reorg by connecting node1 to node2
   */
  private async triggerReorg(node1: AnimicaDevnetClient, node2: AnimicaDevnetClient): Promise<void> {
    // In practice, this would:
    // 1. Restore network connectivity
    // 2. Wait for peer discovery
    // 3. Wait for chain sync
    
    console.log(`[Reorg] Waiting for reorg to propagate...`);
    
    // Poll until node1 sees node2's longer chain
    const maxWait = 30000; // 30 seconds
    const startTime = Date.now();
    
    const node2Head = await node2.getBlockHeight();
    
    while (Date.now() - startTime < maxWait) {
      const node1Head = await node1.getBlockHeight();
      
      if (node1Head >= node2Head - 1) {
        console.log(`[Reorg] Reorg complete, new head: ${node1Head}`);
        return;
      }
      
      await new Promise(resolve => setTimeout(resolve, 1000));
    }
    
    throw new Error('Timeout waiting for reorg');
  }
}

/**
 * Test deposit safety during reorg
 */
export async function testDepositReorgSafety(
  simulator: ReorgSimulator,
  params: {
    depositAddress: string;
    depositAmount: string;
    faucetAddress: string;
    expectedBehavior: 'should_survive' | 'may_disappear';
  }
): Promise<{
  passed: boolean;
  scenario: ReorgScenario;
  message: string;
}> {
  const scenario = await simulator.executeReorg({
    depositAddress: params.depositAddress,
    depositAmount: params.depositAmount,
    faucetAddress: params.faucetAddress,
  });
  
  let passed = false;
  let message = '';
  
  if (params.expectedBehavior === 'should_survive') {
    passed = scenario.depositIncluded;
    message = passed
      ? 'Deposit correctly survived reorg'
      : 'ERROR: Deposit lost during reorg (should have survived)';
  } else {
    // may_disappear: either outcome is acceptable
    passed = true;
    message = scenario.depositIncluded
      ? 'Deposit survived reorg'
      : 'Deposit lost during reorg (expected behavior)';
  }
  
  return { passed, scenario, message };
}

/**
 * Generate reorg test scenarios
 */
export function generateReorgScenarios(): Array<{
  name: string;
  reorgDepth: number;
  expectedBehavior: 'should_survive' | 'may_disappear';
  description: string;
}> {
  return [
    {
      name: 'shallow_reorg',
      reorgDepth: 1,
      expectedBehavior: 'may_disappear',
      description: '1-block reorg: deposit may or may not be included',
    },
    {
      name: 'moderate_reorg',
      reorgDepth: 3,
      expectedBehavior: 'may_disappear',
      description: '3-block reorg: tests confirmation depth',
    },
    {
      name: 'deep_reorg',
      reorgDepth: 6,
      expectedBehavior: 'should_survive',
      description: '6-block reorg: deposits with 6+ confirmations should survive',
    },
    {
      name: 'very_deep_reorg',
      reorgDepth: 12,
      expectedBehavior: 'should_survive',
      description: '12-block reorg: fully confirmed deposits must survive',
    },
  ];
}
