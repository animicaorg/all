/**
 * BitGo Withdrawal Testing Scenario
 * 
 * Tests BitGo withdrawal flow:
 * 1. Request withdrawal
 * 2. Verify ledger lock
 * 3. Simulate approval/broadcast
 * 4. Verify balance debited
 */

import { E2EConfig } from '../config.js';
import { ScenarioResult, TestReport } from '../report.js';
import { Scenario } from '../runner.js';
import { ExchangeAPIClient, AdminAPIClient } from '../http_client.js';
import {
  BitGoMockWithdrawal,
  simulateWithdrawal,
  MockWithdrawalConfig,
} from '../sim/withdrawals/bitgo_mock.js';
import {
  BitGoSandboxWithdrawal,
  executeBatchWithdrawals,
  WithdrawalBitGoSandboxConfig,
} from '../sim/withdrawals/bitgo_sandbox.js';

const scenario: Scenario = {
  name: 'withdrawals_bitgo',
  description: 'BitGo withdrawal with approval and broadcast',
  
  async run(config: E2EConfig, report: TestReport): Promise<ScenarioResult> {
    try {
      console.log('   → Creating test user');
      
      const adminClient = new AdminAPIClient({
        baseURL: config.adminAPI,
      });
      
      // Create user
      const userResponse = await adminClient.createUser({
        email: `withdrawal-bitgo-${Date.now()}@example.com`,
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
      
      // Credit initial balance via admin API (for testing)
      console.log('   → Crediting initial balance');
      await adminClient.post(`/api/admin/users/${userId}/credit`, {
        asset: 'BTC',
        amount: '1.0',
        reason: 'test withdrawal',
      });
      
      // Get initial balance
      console.log('   → Getting initial balance');
      const initialBalanceResponse = await exchangeClient.getBalance();
      
      if (initialBalanceResponse.status !== 200) {
        throw new Error('Failed to get initial balance');
      }
      
      const initialBalance = parseFloat(initialBalanceResponse.data.BTC?.available || '0');
      console.log(`   ✓ Initial BTC balance: ${initialBalance}`);
      
      if (initialBalance < 0.01) {
        throw new Error(`Insufficient balance for withdrawal: ${initialBalance}`);
      }
      
      // Request withdrawal
      const withdrawalAmount = '0.5';
      const destinationAddress = '1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa'; // Genesis address
      
      console.log(`   → Requesting withdrawal of ${withdrawalAmount} BTC`);
      const withdrawalResponse = await exchangeClient.post('/api/v1/withdrawals', {
        asset: 'BTC',
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
      
      const lockedBalance = parseFloat(lockedBalanceResponse.data.BTC?.locked || '0');
      
      if (Math.abs(lockedBalance - parseFloat(withdrawalAmount)) > 0.00001) {
        throw new Error(`Locked balance mismatch: expected ${withdrawalAmount}, got ${lockedBalance}`);
      }
      
      console.log(`   ✓ Balance locked: ${lockedBalance}`);
      
      // Use mock or sandbox simulator
      const useSandbox = config.useBitGoSandbox;
      
      if (useSandbox && config.bitgoAccessToken) {
        console.log('   → Using BitGo Sandbox');
        
        const sandboxConfig: WithdrawalBitGoSandboxConfig = {
          accessToken: config.bitgoAccessToken,
          walletId: 'test-wallet',
          coin: 'tbtc',
        };
        
        const sandbox = new BitGoSandboxWithdrawal(sandboxConfig);
        
        console.log('   → Executing sandbox withdrawal');
        const results = await executeBatchWithdrawals(sandbox, [{
          withdrawalId,
          toAddress: destinationAddress,
          amount: withdrawalAmount,
        }]);
        
        if (results.length === 0 || !results[0].success) {
          throw new Error('Sandbox withdrawal failed');
        }
        
        console.log(`   ✓ Withdrawal broadcast: ${results[0].txHash}`);
      } else {
        console.log('   → Using BitGo Mock Simulator');
        
        const mockConfig: MockWithdrawalConfig = {
          walletId: 'test-wallet',
          coin: 'btc',
        };
        
        const simulator = new BitGoMockWithdrawal(mockConfig);
        
        console.log('   → Simulating withdrawal');
        const result = await simulateWithdrawal(simulator, {
          withdrawalId,
          toAddress: destinationAddress,
          amount: withdrawalAmount,
        });
        
        console.log(`   ✓ Withdrawal simulated: ${result.txHash}`);
      }
      
      // Wait for processing
      console.log('   → Waiting for withdrawal processing (10s)');
      await sleep(10000);
      
      // Check final balance
      console.log('   → Checking final balance');
      const finalBalanceResponse = await exchangeClient.getBalance();
      
      if (finalBalanceResponse.status !== 200) {
        throw new Error('Failed to get final balance');
      }
      
      const finalAvailable = parseFloat(finalBalanceResponse.data.BTC?.available || '0');
      const finalLocked = parseFloat(finalBalanceResponse.data.BTC?.locked || '0');
      
      console.log(`   ✓ Final available: ${finalAvailable}`);
      console.log(`   ✓ Final locked: ${finalLocked}`);
      
      // Verify balance debited
      const expectedFinal = initialBalance - parseFloat(withdrawalAmount);
      const balanceDiff = Math.abs(finalAvailable - expectedFinal);
      
      if (balanceDiff > 0.00001) {
        throw new Error(
          `Balance mismatch: expected ${expectedFinal}, got ${finalAvailable}`
        );
      }
      
      console.log('   ✓ Balance debited correctly');
      
      // Update report metrics
      report.metrics.withdrawals++;
      
      return {
        name: 'withdrawals_bitgo',
        passed: true,
        duration: 0,
        metrics: {
          userId,
          withdrawalId,
          withdrawalAmount,
          destinationAddress,
          initialBalance,
          finalAvailable,
          finalLocked,
          simulator: useSandbox ? 'sandbox' : 'mock',
        },
      };
      
    } catch (error: any) {
      return {
        name: 'withdrawals_bitgo',
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
