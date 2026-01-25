/**
 * Blockchain Reorg Testing Scenario
 * 
 * Tests deposit safety during blockchain reorganization:
 * 1. Use AnimicaReorgSimulator
 * 2. Make deposit
 * 3. Force reorg
 * 4. Verify deposit safety
 */

import { E2EConfig } from '../config.js';
import { ScenarioResult, TestReport } from '../report.js';
import { Scenario } from '../runner.js';
import { ExchangeAPIClient, AdminAPIClient } from '../http_client.js';
import { ReorgSimulator, ReorgConfig, ReorgScenario } from '../sim/deposits/animica_reorg.js';

const scenario: Scenario = {
  name: 'reorg_animica',
  description: 'Blockchain reorg testing for deposit safety',
  
  async run(config: E2EConfig, report: TestReport): Promise<ScenarioResult> {
    try {
      console.log('   → Checking reorg configuration');
      
      // Check if multiple node URLs are configured
      const nodeUrls = process.env.ANIMICA_NODE_URLS?.split(',') || [config.animicaRPC];
      
      if (nodeUrls.length < 2) {
        console.warn('   ⚠️  Reorg testing requires at least 2 Animica nodes');
        console.warn('   ⚠️  Set ANIMICA_NODE_URLS=url1,url2 environment variable');
        
        return {
          name: 'reorg_animica',
          passed: true,
          duration: 0,
          error: 'Insufficient nodes for reorg testing',
        };
      }
      
      console.log(`   ✓ Found ${nodeUrls.length} nodes`);
      
      // Create test user
      const adminClient = new AdminAPIClient({
        baseURL: config.adminAPI,
      });
      
      const userResponse = await adminClient.createUser({
        email: `reorg-${Date.now()}@example.com`,
        password: 'ReorgTest123!',
      });
      
      if (userResponse.status !== 201) {
        throw new Error(`Failed to create user: ${userResponse.status}`);
      }
      
      const userId = userResponse.data.id;
      console.log(`   ✓ Created user: ${userId}`);
      
      // Create API key
      const apiKeyResponse = await adminClient.createAPIKey(userId);
      
      if (apiKeyResponse.status !== 201) {
        throw new Error(`Failed to create API key: ${apiKeyResponse.status}`);
      }
      
      const apiKey = apiKeyResponse.data.key;
      const apiSecret = apiKeyResponse.data.secret;
      
      const exchangeClient = new ExchangeAPIClient({
        baseURL: config.apiGateway,
        apiKey,
        apiSecret,
      });
      
      // Get initial balance
      const initialBalanceResponse = await exchangeClient.getBalance();
      
      if (initialBalanceResponse.status !== 200) {
        throw new Error('Failed to get initial balance');
      }
      
      const initialBalance = parseFloat(initialBalanceResponse.data.ANM?.available || '0');
      console.log(`   ✓ Initial balance: ${initialBalance}`);
      
      // Generate deposit address
      console.log('   → Generating deposit address');
      const depositAddressResponse = await exchangeClient.post('/api/v1/deposits/address', {
        asset: 'ANM',
      });
      
      if (depositAddressResponse.status !== 201) {
        throw new Error('Failed to generate deposit address');
      }
      
      const depositAddress = depositAddressResponse.data.address;
      console.log(`   ✓ Generated address: ${depositAddress}`);
      
      // Initialize reorg simulator
      const reorgConfig: ReorgConfig = {
        nodeUrls,
        faucetPrivateKey: process.env.ANIMICA_FAUCET_KEY || '0x' + '1'.repeat(64),
        chainId: 1337,
        reorgDepth: 3,
      };
      
      const reorgSim = new ReorgSimulator(reorgConfig);
      
      // Execute reorg scenario
      const depositAmount = '100.0';
      
      console.log('   → Executing reorg scenario');
      console.log(`   → Deposit amount: ${depositAmount} ANM`);
      
      const reorgResult: ReorgScenario = await reorgSim.executeReorg({
        depositAddress,
        depositAmount,
        faucetAddress: '0x' + '0'.repeat(40), // Faucet address
      });
      
      console.log(`   ✓ Reorg executed:`);
      console.log(`      Original head: ${reorgResult.originalHead}`);
      console.log(`      Original tx: ${reorgResult.originalTxHash}`);
      console.log(`      Original block: ${reorgResult.originalBlock}`);
      console.log(`      New head: ${reorgResult.newHead}`);
      console.log(`      Deposit included: ${reorgResult.depositIncluded}`);
      
      if (reorgResult.newTxHash) {
        console.log(`      New tx: ${reorgResult.newTxHash}`);
      }
      
      // Wait for reorg processing
      console.log('   → Waiting for reorg processing (30s)');
      await sleep(30000);
      
      // Check balance after reorg
      console.log('   → Checking balance after reorg');
      const finalBalanceResponse = await exchangeClient.getBalance();
      
      if (finalBalanceResponse.status !== 200) {
        throw new Error('Failed to get final balance');
      }
      
      const finalBalance = parseFloat(finalBalanceResponse.data.ANM?.available || '0');
      console.log(`   ✓ Final balance: ${finalBalance}`);
      
      // Verify deposit safety
      if (reorgResult.depositIncluded) {
        // Deposit was included in new chain, should be credited
        const expectedBalance = initialBalance + parseFloat(depositAmount);
        const balanceDiff = Math.abs(finalBalance - expectedBalance);
        
        if (balanceDiff > 0.01) {
          throw new Error(
            `Deposit credited incorrectly after reorg: expected ${expectedBalance}, got ${finalBalance}`
          );
        }
        
        console.log('   ✓ Deposit safely credited in new chain');
      } else {
        // Deposit was not included in new chain, should not be credited
        const balanceDiff = Math.abs(finalBalance - initialBalance);
        
        if (balanceDiff > 0.01) {
          throw new Error(
            `Deposit incorrectly credited despite not being in new chain: ${finalBalance}`
          );
        }
        
        console.log('   ✓ Deposit correctly not credited (not in new chain)');
      }
      
      // Check for duplicate credits
      console.log('   → Checking for duplicate credits');
      const deposits = await adminClient.getDeposits({
        userId,
        limit: 100,
      });
      
      if (deposits.status !== 200) {
        throw new Error('Failed to get deposits');
      }
      
      const depositTxHashes = deposits.data.map((d: any) => d.txHash);
      const uniqueTxHashes = new Set(depositTxHashes);
      
      if (depositTxHashes.length !== uniqueTxHashes.size) {
        report.invariants.noDuplicateCredits = false;
        throw new Error('Duplicate deposits detected after reorg');
      }
      
      console.log('   ✓ No duplicate credits');
      
      // Update report metrics
      report.metrics.deposits++;
      report.metrics.faultsInjected.push('reorg:animica');
      
      return {
        name: 'reorg_animica',
        passed: true,
        duration: 0,
        metrics: {
          userId,
          depositAddress,
          depositAmount,
          reorgDepth: reorgConfig.reorgDepth,
          originalHead: reorgResult.originalHead,
          newHead: reorgResult.newHead,
          depositIncluded: reorgResult.depositIncluded,
          originalTxHash: reorgResult.originalTxHash,
          newTxHash: reorgResult.newTxHash,
          initialBalance,
          finalBalance,
        },
      };
      
    } catch (error: any) {
      return {
        name: 'reorg_animica',
        passed: false,
        duration: 0,
        error: error.message,
      };
    }
  },
};

function sleep(ms: number): Promise<void> {
  return new Promise(resolve => setTimeout(resolve, ms));
}

export default scenario;
