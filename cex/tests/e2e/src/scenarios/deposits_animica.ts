/**
 * Animica Deposit Testing Scenario
 * 
 * Tests Animica deposit flow:
 * 1. Use AnimicaDevnetSimulator
 * 2. Generate deposit address
 * 3. Send ANM from faucet
 * 4. Wait for confirmations
 * 5. Verify credit
 */

import { E2EConfig } from '../config.js';
import { ScenarioResult, TestReport } from '../report.js';
import { Scenario } from '../runner.js';
import { ExchangeAPIClient, AdminAPIClient } from '../http_client.js';
import {
  AnimicaDevnetClient,
  simulateDeposit,
  AnimicaDepositConfig,
} from '../sim/deposits/animica_devnet.js';

const scenario: Scenario = {
  name: 'deposits_animica',
  description: 'Animica devnet deposit with confirmation tracking',
  
  async run(config: E2EConfig, report: TestReport): Promise<ScenarioResult> {
    try {
      console.log('   → Creating test user');
      
      const adminClient = new AdminAPIClient({
        baseURL: config.adminAPI,
      });
      
      // Create user
      const userResponse = await adminClient.createUser({
        email: `deposit-animica-${Date.now()}@example.com`,
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
      
      const initialBalance = parseFloat(initialBalanceResponse.data.ANM?.available || '0');
      console.log(`   ✓ Initial ANM balance: ${initialBalance}`);
      
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
      
      // Initialize Animica devnet client
      const animicaConfig: AnimicaDepositConfig = {
        rpcUrl: config.animicaRPC,
        faucetPrivateKey: process.env.ANIMICA_FAUCET_KEY || '0x' + '1'.repeat(64),
        chainId: 1337,
        requiredConfirmations: 3,
      };
      
      const devnetClient = new AnimicaDevnetClient(animicaConfig);
      
      // Get current block height
      const startHeight = await devnetClient.getBlockHeight();
      console.log(`   ✓ Current block height: ${startHeight}`);
      
      // Simulate deposit
      const depositAmount = '100.0'; // 100 ANM
      
      console.log(`   → Sending ${depositAmount} ANM from faucet`);
      const depositResult = await simulateDeposit(devnetClient, {
        toAddress: depositAddress,
        amount: depositAmount,
        fromPrivateKey: animicaConfig.faucetPrivateKey,
      });
      
      console.log(`   ✓ Transaction sent: ${depositResult.txHash}`);
      console.log(`   → Block: ${depositResult.blockNumber}`);
      
      // Wait for confirmations
      const requiredConfirmations = 3;
      console.log(`   → Waiting for ${requiredConfirmations} confirmations`);
      
      let currentHeight = await devnetClient.getBlockHeight();
      const targetHeight = depositResult.blockNumber + requiredConfirmations;
      
      while (currentHeight < targetHeight) {
        await sleep(2000);
        currentHeight = await devnetClient.getBlockHeight();
        console.log(`   → Height: ${currentHeight}/${targetHeight}`);
      }
      
      console.log('   ✓ Confirmations reached');
      
      // Wait for processing
      console.log('   → Waiting for deposit processing (10s)');
      await sleep(10000);
      
      // Check updated balance
      console.log('   → Checking updated balance');
      const finalBalanceResponse = await exchangeClient.getBalance();
      
      if (finalBalanceResponse.status !== 200) {
        throw new Error('Failed to get final balance');
      }
      
      const finalBalance = parseFloat(finalBalanceResponse.data.ANM?.available || '0');
      console.log(`   ✓ Final ANM balance: ${finalBalance}`);
      
      // Verify credit
      const expectedBalance = initialBalance + parseFloat(depositAmount);
      const balanceDiff = Math.abs(finalBalance - expectedBalance);
      
      if (balanceDiff > 0.01) {
        throw new Error(
          `Balance mismatch: expected ${expectedBalance}, got ${finalBalance}`
        );
      }
      
      console.log('   ✓ Balance updated correctly');
      
      // Update report metrics
      report.metrics.deposits++;
      
      return {
        name: 'deposits_animica',
        passed: true,
        duration: 0,
        metrics: {
          userId,
          depositAddress,
          depositAmount,
          txHash: depositResult.txHash,
          blockNumber: depositResult.blockNumber,
          confirmations: requiredConfirmations,
          initialBalance,
          finalBalance,
        },
      };
      
    } catch (error: any) {
      return {
        name: 'deposits_animica',
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
