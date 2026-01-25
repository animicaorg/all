/**
 * BitGo Deposit Testing Scenario
 * 
 * Tests BitGo deposit flow:
 * 1. Use BitGoMockSimulator or BitGoSandboxSimulator
 * 2. Generate deposit address
 * 3. Simulate deposit transaction
 * 4. Wait for webhook and credit
 * 5. Verify balance updated
 */

import { E2EConfig } from '../config.js';
import { ScenarioResult, TestReport } from '../report.js';
import { Scenario } from '../runner.js';
import { ExchangeAPIClient, AdminAPIClient } from '../http_client.js';
import {
  BitGoMockSimulator,
  MockDepositConfig,
  simulateDeposit,
} from '../sim/deposits/bitgo_mock.js';
import {
  BitGoSandbox,
  createSandboxDeposit,
  DepositBitGoSandboxConfig,
} from '../sim/deposits/bitgo_sandbox.js';

const scenario: Scenario = {
  name: 'deposits_bitgo',
  description: 'BitGo deposit flow with webhook simulation',
  
  async run(config: E2EConfig, report: TestReport): Promise<ScenarioResult> {
    try {
      console.log('   → Creating test user');
      
      const adminClient = new AdminAPIClient({
        baseURL: config.adminAPI,
      });
      
      // Create user
      const userResponse = await adminClient.createUser({
        email: `deposit-bitgo-${Date.now()}@example.com`,
        password: 'DepositTest123!',
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
      console.log('   → Getting initial balance');
      const initialBalanceResponse = await exchangeClient.getBalance();
      
      if (initialBalanceResponse.status !== 200) {
        throw new Error('Failed to get initial balance');
      }
      
      const initialBalance = parseFloat(initialBalanceResponse.data.BTC?.available || '0');
      console.log(`   ✓ Initial BTC balance: ${initialBalance}`);
      
      // Generate deposit address
      console.log('   → Generating deposit address');
      const depositAddressResponse = await exchangeClient.post('/api/v1/deposits/address', {
        asset: 'BTC',
      });
      
      if (depositAddressResponse.status !== 201) {
        throw new Error('Failed to generate deposit address');
      }
      
      const depositAddress = depositAddressResponse.data.address;
      console.log(`   ✓ Generated address: ${depositAddress}`);
      
      // Use mock or sandbox simulator
      const useSandbox = config.useBitGoSandbox;
      const depositAmount = '0.001'; // 0.001 BTC
      
      if (useSandbox && config.bitgoAccessToken && config.bitgoWebhookSecret) {
        console.log('   → Using BitGo Sandbox');
        
        const sandboxConfig: DepositBitGoSandboxConfig = {
          webhookUrl: `${config.apiGateway}/webhooks/bitgo`,
          webhookSecret: config.bitgoWebhookSecret,
          walletId: 'test-wallet',
          coin: 'tbtc',
          accessToken: config.bitgoAccessToken,
        };
        
        const sandbox = new BitGoSandbox(sandboxConfig);
        
        console.log('   → Creating sandbox deposit');
        const depositResult = await createSandboxDeposit(sandbox, {
          address: depositAddress,
          amount: depositAmount,
          confirmations: 3,
        });
        
        console.log(`   ✓ Deposit created: ${depositResult.txHash}`);
      } else {
        console.log('   → Using BitGo Mock Simulator');
        
        const mockConfig: MockDepositConfig = {
          webhookUrl: `${config.apiGateway}/webhooks/bitgo`,
          webhookSecret: config.bitgoWebhookSecret || 'test-secret',
          walletId: 'test-wallet',
          coin: 'btc',
        };
        
        const simulator = new BitGoMockSimulator(mockConfig);
        
        console.log('   → Simulating deposit');
        const depositResult = await simulateDeposit(simulator, {
          address: depositAddress,
          amount: depositAmount,
          confirmations: 3,
        });
        
        console.log(`   ✓ Deposit simulated: ${depositResult.txHash}`);
      }
      
      // Wait for webhook processing
      console.log('   → Waiting for webhook processing (30s)');
      await sleep(30000);
      
      // Check updated balance
      console.log('   → Checking updated balance');
      const finalBalanceResponse = await exchangeClient.getBalance();
      
      if (finalBalanceResponse.status !== 200) {
        throw new Error('Failed to get final balance');
      }
      
      const finalBalance = parseFloat(finalBalanceResponse.data.BTC?.available || '0');
      console.log(`   ✓ Final BTC balance: ${finalBalance}`);
      
      // Verify credit
      const expectedBalance = initialBalance + parseFloat(depositAmount);
      const balanceDiff = Math.abs(finalBalance - expectedBalance);
      
      if (balanceDiff > 0.00001) {
        throw new Error(
          `Balance mismatch: expected ${expectedBalance}, got ${finalBalance}`
        );
      }
      
      console.log('   ✓ Balance updated correctly');
      
      // Update report metrics
      report.metrics.deposits++;
      
      return {
        name: 'deposits_bitgo',
        passed: true,
        duration: 0,
        metrics: {
          userId,
          depositAddress,
          depositAmount,
          initialBalance,
          finalBalance,
          simulator: useSandbox ? 'sandbox' : 'mock',
        },
      };
      
    } catch (error: any) {
      return {
        name: 'deposits_bitgo',
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
