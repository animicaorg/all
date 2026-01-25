/**
 * Reconciliation Proof Scenario
 * 
 * Comprehensive end-to-end test that generates cryptographic proof:
 * 1. Run trading activity (market maker + takers)
 * 2. Execute deposits and withdrawals
 * 3. Optionally inject chaos
 * 4. Take ledger snapshots
 * 5. Verify all invariants
 * 6. Generate hashchain proof
 * 7. Save proof bundle
 */

import { E2EConfig } from '../config.js';
import { ScenarioResult, TestReport } from '../report.js';
import { Scenario } from '../runner.js';
import { ExchangeAPIClient, AdminAPIClient } from '../http_client.js';
import { WSClient } from '../ws_client.js';
import { MarketMaker, MarketMakerConfig } from '../sim/mm/maker.js';
import {
  BitGoMockSimulator,
  MockDepositConfig,
  simulateDeposit,
} from '../sim/deposits/bitgo_mock.js';
import {
  BitGoMockWithdrawal,
  simulateWithdrawal,
  MockWithdrawalConfig,
} from '../sim/withdrawals/bitgo_mock.js';
import { DockerChaos } from '../sim/chaos/docker.js';
import { takeLedgerSnapshot, LedgerSnapshot } from '../sim/reconcile/ledger_snapshot.js';
import { checkAllInvariants } from '../sim/reconcile/invariants.js';
import { generateProofBundle } from '../sim/reconcile/proof_bundle.js';
import * as fs from 'fs/promises';
import * as path from 'path';

const scenario: Scenario = {
  name: 'reconciliation_proof',
  description: 'Full reconciliation proof with invariant checks',
  
  async run(config: E2EConfig, report: TestReport): Promise<ScenarioResult> {
    try {
      const adminClient = new AdminAPIClient({
        baseURL: config.adminAPI,
      });
      
      // Phase 1: Run market maker
      console.log('   → Phase 1: Running market maker');
      
      const mmUserResponse = await adminClient.createUser({
        email: `mm-reconcile-${Date.now()}@example.com`,
        password: 'MMPassword123!',
      });
      
      if (mmUserResponse.status !== 201) {
        throw new Error('Failed to create MM user');
      }
      
      const mmUserId = mmUserResponse.data.id;
      const mmKeyResponse = await adminClient.createAPIKey(mmUserId);
      
      if (mmKeyResponse.status !== 201) {
        throw new Error('Failed to create MM API key');
      }
      
      const mmApiKey = mmKeyResponse.data.key;
      const mmApiSecret = mmKeyResponse.data.secret;
      
      const mmExchangeClient = new ExchangeAPIClient({
        baseURL: config.apiGateway,
        apiKey: mmApiKey,
        apiSecret: mmApiSecret,
      });
      
      const mmWsClient = new WSClient({
        url: config.websocketURL,
        apiKey: mmApiKey,
        reconnect: true,
      });
      
      await mmWsClient.connect();
      
      const market = config.markets[0];
      const mmConfig: MarketMakerConfig = {
        market,
        strategy: 'tight_spread',
        strategyConfig: {
          spread: 0.002,
          orderLevels: 3,
          orderSize: 1.0,
        },
        inventory: {
          targetBaseBalance: 50.0,
          targetQuoteBalance: 50000.0,
          maxSkew: 0.3,
        },
        riskLimits: {
          maxPositionValue: 100000,
          maxOrderValue: 10000,
          maxDailyVolume: 1000000,
        },
        tickSize: 0.01,
        minOrderSize: 0.001,
        quoteRefreshInterval: 1000,
        randomSeed: config.seed,
      };
      
      const marketMaker = new MarketMaker(mmConfig, mmExchangeClient, mmWsClient);
      await marketMaker.start();
      
      // Run for 30 seconds
      await sleep(30000);
      
      await marketMaker.stop();
      const mmStats = marketMaker.getStats();
      
      console.log(`   ✓ Market maker: ${mmStats.ordersPlaced} orders, ${mmStats.trades} trades`);
      mmWsClient.disconnect();
      
      // Phase 2: Execute deposits
      console.log('   → Phase 2: Executing deposits');
      
      const depositUserResponse = await adminClient.createUser({
        email: `deposit-reconcile-${Date.now()}@example.com`,
        password: 'DepositTest123!',
      });
      
      if (depositUserResponse.status !== 201) {
        throw new Error('Failed to create deposit user');
      }
      
      const depositUserId = depositUserResponse.data.id;
      const depositKeyResponse = await adminClient.createAPIKey(depositUserId);
      
      if (depositKeyResponse.status !== 201) {
        throw new Error('Failed to create deposit API key');
      }
      
      const depositApiKey = depositKeyResponse.data.key;
      const depositApiSecret = depositKeyResponse.data.secret;
      
      const depositClient = new ExchangeAPIClient({
        baseURL: config.apiGateway,
        apiKey: depositApiKey,
        apiSecret: depositApiSecret,
      });
      
      const depositAddressResponse = await depositClient.post('/api/v1/deposits/address', {
        asset: 'BTC',
      });
      
      if (depositAddressResponse.status !== 201) {
        throw new Error('Failed to generate deposit address');
      }
      
      const depositAddress = depositAddressResponse.data.address;
      
      const depositConfig: MockDepositConfig = {
        webhookUrl: `${config.apiGateway}/webhooks/bitgo`,
        webhookSecret: config.bitgoWebhookSecret || 'test-secret',
        walletId: 'test-wallet',
        coin: 'btc',
      };
      
      const depositSim = new BitGoMockSimulator(depositConfig);
      await simulateDeposit(depositSim, {
        address: depositAddress,
        amount: '0.5',
        confirmations: 3,
      });
      
      console.log('   ✓ Deposit simulated');
      await sleep(10000); // Wait for processing
      
      // Phase 3: Execute withdrawal
      console.log('   → Phase 3: Executing withdrawal');
      
      await adminClient.post(`/api/admin/users/${depositUserId}/credit`, {
        asset: 'BTC',
        amount: '1.0',
        reason: 'test withdrawal',
      });
      
      const withdrawalResponse = await depositClient.post('/api/v1/withdrawals', {
        asset: 'BTC',
        amount: '0.3',
        address: '1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa',
      });
      
      if (withdrawalResponse.status !== 201) {
        throw new Error('Failed to request withdrawal');
      }
      
      const withdrawalId = withdrawalResponse.data.id;
      
      const withdrawalConfig: MockWithdrawalConfig = {
        walletId: 'test-wallet',
        coin: 'btc',
      };
      
      const withdrawalSim = new BitGoMockWithdrawal(withdrawalConfig);
      await simulateWithdrawal(withdrawalSim, {
        withdrawalId,
        toAddress: '1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa',
        amount: '0.3',
      });
      
      console.log('   ✓ Withdrawal simulated');
      await sleep(10000); // Wait for processing
      
      // Phase 4: Chaos (optional)
      if (config.enableChaos) {
        console.log('   → Phase 4: Injecting chaos');
        
        try {
          const chaos = new DockerChaos({});
          const containers = await chaos.listContainers({
            label: 'com.animica.cex=true',
          });
          
          if (containers.length > 0) {
            const target = containers[0];
            await chaos.restartContainer(target.id);
            await sleep(5000);
            console.log(`   ✓ Restarted ${target.name}`);
          }
        } catch (error: any) {
          console.warn(`   ⚠️  Chaos injection failed: ${error.message}`);
        }
      }
      
      // Phase 5: Take ledger snapshot
      console.log('   → Phase 5: Taking ledger snapshot');
      const snapshot = await takeLedgerSnapshot(adminClient);
      console.log('   ✓ Snapshot captured');
      
      // Phase 6: Verify invariants
      console.log('   → Phase 6: Verifying invariants');
      const invariantReport = await checkAllInvariants(snapshot, adminClient);
      
      // Update report invariants
      for (const result of invariantReport.results) {
        switch (result.name) {
          case 'double_entry':
            report.invariants.ledgerDoubleEntryOk = result.passed;
            break;
          case 'solvency':
            report.invariants.solvencyOk = result.passed;
            break;
          case 'no_negative_balances':
            report.invariants.noNegativeBalances = result.passed;
            break;
          case 'no_duplicate_credits':
            report.invariants.noDuplicateCredits = result.passed;
            break;
          case 'trade_ledger_consistency':
            report.invariants.tradeLedgerConsistencyOk = result.passed;
            break;
        }
      }
      
      console.log(`   ✓ Invariants: ${invariantReport.summary.passed}/${invariantReport.summary.total} passed`);
      
      // Phase 7: Generate proof bundle
      console.log('   → Phase 7: Generating proof bundle');
      const proofBundle = await generateProofBundle(snapshot, config.seed);
      console.log('   ✓ Proof bundle generated');
      
      // Save proof bundle
      const proofPath = path.join(config.artifactsDir, `proof-${Date.now()}.json`);
      await fs.mkdir(config.artifactsDir, { recursive: true });
      await fs.writeFile(proofPath, JSON.stringify(proofBundle, null, 2), 'utf-8');
      report.proofBundlePath = proofPath;
      
      console.log(`   ✓ Proof bundle saved: ${proofPath}`);
      
      // Update metrics
      report.metrics.ordersSubmitted += mmStats.ordersPlaced;
      report.metrics.trades += mmStats.trades;
      report.metrics.deposits++;
      report.metrics.withdrawals++;
      
      return {
        name: 'reconciliation_proof',
        passed: invariantReport.allPassed,
        duration: 0,
        error: invariantReport.allPassed ? undefined : 'One or more invariants failed',
        metrics: {
          proofBundlePath: proofPath,
          rootHash: proofBundle.rootHash,
          eventCount: proofBundle.eventCount,
          invariantsPassed: invariantReport.summary.passed,
          invariantsTotal: invariantReport.summary.total,
          ordersPlaced: mmStats.ordersPlaced,
          trades: mmStats.trades,
          deposits: 1,
          withdrawals: 1,
        },
      };
      
    } catch (error: any) {
      return {
        name: 'reconciliation_proof',
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
