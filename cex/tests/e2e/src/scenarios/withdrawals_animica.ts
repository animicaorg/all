/**
 * Animica Withdrawal Testing Scenario
 * 
 * Tests Animica withdrawal flow:
 * 1. Request withdrawal
 * 2. Broadcast transaction
 * 3. Verify on-chain delivery
 */

import { E2EConfig } from '../config.js';
import { ScenarioResult, TestReport } from '../report.js';
import { Scenario } from '../runner.js';
import { ExchangeAPIClient, AdminAPIClient } from '../http_client.js';
import {
  AnimicaWithdrawalClient,
  testWithdrawalFlow,
  AnimicaWithdrawalConfig,
} from '../sim/withdrawals/animica_devnet.js';

const scenario: Scenario = {
  name: 'withdrawals_animica',
  description: 'Animica withdrawal with on-chain verification',
  
  async run(config: E2EConfig, report: TestReport): Promise<ScenarioResult> {
    try {
      console.log('   → Creating test user');
      
      const adminClient = new AdminAPIClient({
        baseURL: config.adminAPI,
      });
      
      // Create user
      const userResponse = await adminClient.createUser({
        email: `withdrawal-animica-${Date.now()}@example.com`,
        password: 'WithdrawalTest123!',
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
      
      // Credit initial balance via admin API
      console.log('   → Crediting initial balance');
      await adminClient.post(`/api/admin/users/${userId}/credit`, {
        asset: 'ANM',
        amount: '1000.0',
        reason: 'test withdrawal',
      });
      
      // Get initial balance
      console.log('   → Getting initial balance');
      const initialBalanceResponse = await exchangeClient.getBalance();
      
      if (initialBalanceResponse.status !== 200) {
        throw new Error('Failed to get initial balance');
      }
      
      const initialBalance = parseFloat(initialBalanceResponse.data.ANM?.available || '0');
      console.log(`   ✓ Initial ANM balance: ${initialBalance}`);
      
      if (initialBalance < 10) {
        throw new Error(`Insufficient balance for withdrawal: ${initialBalance}`);
      }
      
      // Generate destination address
      const destinationAddress = '0x' + '9'.repeat(40); // Test address
      
      // Request withdrawal
      const withdrawalAmount = '500.0';
      
      console.log(`   → Requesting withdrawal of ${withdrawalAmount} ANM`);
      const withdrawalResponse = await exchangeClient.post('/api/v1/withdrawals', {
        asset: 'ANM',
        amount: withdrawalAmount,
        address: destinationAddress,
      });
      
      if (withdrawalResponse.status !== 201) {
        throw new Error(`Failed to request withdrawal: ${withdrawalResponse.status}`);
      }
      
      const withdrawalId = withdrawalResponse.data.id;
      console.log(`   ✓ Withdrawal requested: ${withdrawalId}`);
      
      // Check balance locked
      console.log('   → Verifying balance locked');
      const lockedBalanceResponse = await exchangeClient.getBalance();
      
      if (lockedBalanceResponse.status !== 200) {
        throw new Error('Failed to get locked balance');
      }
      
      const lockedBalance = parseFloat(lockedBalanceResponse.data.ANM?.locked || '0');
      
      if (Math.abs(lockedBalance - parseFloat(withdrawalAmount)) > 0.01) {
        throw new Error(`Locked balance mismatch: expected ${withdrawalAmount}, got ${lockedBalance}`);
      }
      
      console.log(`   ✓ Balance locked: ${lockedBalance}`);
      
      // Initialize Animica withdrawal client
      const animicaConfig: AnimicaWithdrawalConfig = {
        rpcUrl: config.animicaRPC,
        hotWalletPrivateKey: process.env.ANIMICA_HOT_WALLET_KEY || '0x' + '2'.repeat(64),
        chainId: 1337,
      };
      
      const withdrawalClient = new AnimicaWithdrawalClient(animicaConfig);
      
      // Test withdrawal flow
      console.log('   → Broadcasting withdrawal transaction');
      const withdrawalResult = await testWithdrawalFlow(withdrawalClient, {
        withdrawalId,
        toAddress: destinationAddress,
        amount: withdrawalAmount,
      });
      
      console.log(`   ✓ Transaction broadcast: ${withdrawalResult.txHash}`);
      console.log(`   → Block: ${withdrawalResult.blockNumber}`);
      
      // Verify on-chain
      console.log('   → Verifying on-chain delivery');
      const balance = await withdrawalClient.getBalance(destinationAddress);
      
      if (parseFloat(balance) < parseFloat(withdrawalAmount)) {
        throw new Error(
          `On-chain balance verification failed: expected at least ${withdrawalAmount}, got ${balance}`
        );
      }
      
      console.log(`   ✓ On-chain balance: ${balance}`);
      
      // Wait for processing
      console.log('   → Waiting for withdrawal processing (10s)');
      await sleep(10000);
      
      // Check final balance
      console.log('   → Checking final balance');
      const finalBalanceResponse = await exchangeClient.getBalance();
      
      if (finalBalanceResponse.status !== 200) {
        throw new Error('Failed to get final balance');
      }
      
      const finalAvailable = parseFloat(finalBalanceResponse.data.ANM?.available || '0');
      const finalLocked = parseFloat(finalBalanceResponse.data.ANM?.locked || '0');
      
      console.log(`   ✓ Final available: ${finalAvailable}`);
      console.log(`   ✓ Final locked: ${finalLocked}`);
      
      // Verify balance debited
      const expectedFinal = initialBalance - parseFloat(withdrawalAmount);
      const balanceDiff = Math.abs(finalAvailable - expectedFinal);
      
      if (balanceDiff > 0.01) {
        throw new Error(
          `Balance mismatch: expected ${expectedFinal}, got ${finalAvailable}`
        );
      }
      
      console.log('   ✓ Balance debited correctly');
      
      // Update report metrics
      report.metrics.withdrawals++;
      
      return {
        name: 'withdrawals_animica',
        passed: true,
        duration: 0,
        metrics: {
          userId,
          withdrawalId,
          withdrawalAmount,
          destinationAddress,
          txHash: withdrawalResult.txHash,
          blockNumber: withdrawalResult.blockNumber,
          initialBalance,
          finalAvailable,
          finalLocked,
          onChainBalance: balance,
        },
      };
      
    } catch (error: any) {
      return {
        name: 'withdrawals_animica',
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
