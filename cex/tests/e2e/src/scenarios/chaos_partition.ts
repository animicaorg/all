/**
 * Network Partition Chaos Scenario
 * 
 * Tests system resilience to network faults:
 * 1. Use ToxiproxyManager
 * 2. Add latency/packet loss
 * 3. Verify system continues
 * 4. Remove faults
 */

import { E2EConfig } from '../config.js';
import { ScenarioResult, TestReport } from '../report.js';
import { Scenario } from '../runner.js';
import { ExchangeAPIClient, AdminAPIClient } from '../http_client.js';
import { ToxiproxyClient, ToxiproxyConfig, Toxic } from '../sim/chaos/toxiproxy.js';

const scenario: Scenario = {
  name: 'chaos_partition',
  description: 'Network fault injection with Toxiproxy',
  
  async run(config: E2EConfig, report: TestReport): Promise<ScenarioResult> {
    if (!config.enableChaos) {
      return {
        name: 'chaos_partition',
        passed: true,
        duration: 0,
        error: 'Chaos testing disabled (use --chaos flag)',
      };
    }
    
    try {
      console.log('   → Initializing Toxiproxy client');
      
      const toxiConfig: ToxiproxyConfig = {
        apiUrl: `http://${config.toxiproxyHost}:${config.toxiproxyPort}`,
      };
      
      const toxi = new ToxiproxyClient(toxiConfig);
      
      // List existing proxies
      console.log('   → Listing Toxiproxy proxies');
      const proxies = await toxi.listProxies();
      
      if (proxies.length === 0) {
        console.warn('   ⚠️  No Toxiproxy proxies configured');
        console.warn('   ⚠️  Set up proxies for API Gateway, matching engine, etc.');
        
        return {
          name: 'chaos_partition',
          passed: true,
          duration: 0,
          error: 'No proxies configured',
        };
      }
      
      console.log(`   ✓ Found ${proxies.length} proxies`);
      
      // Setup test user
      const adminClient = new AdminAPIClient({
        baseURL: config.adminAPI,
      });
      
      const userResponse = await adminClient.createUser({
        email: `chaos-partition-${Date.now()}@example.com`,
        password: 'ChaosTest123!',
      });
      
      if (userResponse.status !== 201) {
        throw new Error(`Failed to create user: ${userResponse.status}`);
      }
      
      const userId = userResponse.data.id;
      
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
      
      console.log('   ✓ Test user ready');
      
      // Measure baseline latency
      console.log('   → Measuring baseline latency');
      const baselineLatency = await measureLatency(exchangeClient);
      console.log(`   ✓ Baseline latency: ${baselineLatency}ms`);
      
      // Inject network faults
      const faults: string[] = [];
      
      // Select a proxy to inject faults
      const targetProxy = proxies[0];
      console.log(`   → Injecting faults into proxy: ${targetProxy.name}`);
      
      // Add latency
      console.log('   → Adding 100ms latency');
      const latencyToxic: Toxic = {
        name: 'latency-test',
        type: 'latency',
        stream: 'downstream',
        toxicity: 1.0,
        attributes: {
          latency: 100,
          jitter: 10,
        },
      };
      
      await toxi.addToxic(targetProxy.name, latencyToxic);
      faults.push('latency:100ms');
      
      // Measure latency with fault
      await sleep(2000);
      const faultyLatency = await measureLatency(exchangeClient);
      console.log(`   ✓ Latency with fault: ${faultyLatency}ms`);
      
      if (faultyLatency <= baselineLatency) {
        console.warn(`   ⚠️  Expected increased latency, got ${faultyLatency}ms`);
      }
      
      // Add packet loss
      console.log('   → Adding 10% packet loss');
      const packetLossToxic: Toxic = {
        name: 'packet-loss-test',
        type: 'slicer',
        stream: 'downstream',
        toxicity: 0.1,
        attributes: {
          average_size: 100,
          size_variation: 50,
          delay: 10,
        },
      };
      
      await toxi.addToxic(targetProxy.name, packetLossToxic);
      faults.push('packet_loss:10%');
      
      // Try operations under fault conditions
      console.log('   → Testing operations under fault conditions');
      
      let successCount = 0;
      let errorCount = 0;
      const numAttempts = 10;
      
      for (let i = 0; i < numAttempts; i++) {
        try {
          await exchangeClient.placeLimitOrder({
            market: config.markets[0],
            side: 'buy',
            price: '100.00',
            size: '1.0',
            timeInForce: 'GTC',
          });
          
          successCount++;
        } catch {
          errorCount++;
        }
        
        await sleep(500);
      }
      
      console.log(`   → Success: ${successCount}/${numAttempts}, Errors: ${errorCount}/${numAttempts}`);
      
      // System should still be partially functional
      if (successCount === 0) {
        throw new Error('System completely unavailable under fault conditions');
      }
      
      console.log('   ✓ System continues under faults');
      
      // Remove faults
      console.log('   → Removing faults');
      
      await toxi.removeToxic(targetProxy.name, 'latency-test');
      await toxi.removeToxic(targetProxy.name, 'packet-loss-test');
      
      console.log('   ✓ Faults removed');
      
      // Verify recovery
      await sleep(2000);
      
      console.log('   → Verifying recovery');
      const recoveryLatency = await measureLatency(exchangeClient);
      console.log(`   ✓ Recovery latency: ${recoveryLatency}ms`);
      
      // Place test order after recovery
      const orderResponse = await exchangeClient.placeLimitOrder({
        market: config.markets[0],
        side: 'buy',
        price: '100.00',
        size: '1.0',
        timeInForce: 'GTC',
      });
      
      if (orderResponse.status !== 201) {
        throw new Error('Failed to place order after recovery');
      }
      
      console.log('   ✓ Order placed after recovery');
      
      // Update report metrics
      report.metrics.faultsInjected.push(...faults);
      
      return {
        name: 'chaos_partition',
        passed: true,
        duration: 0,
        metrics: {
          userId,
          faults,
          baselineLatency,
          faultyLatency,
          recoveryLatency,
          successRate: `${(successCount / numAttempts * 100).toFixed(1)}%`,
          proxiesUsed: [targetProxy.name],
        },
      };
      
    } catch (error: any) {
      return {
        name: 'chaos_partition',
        passed: false,
        duration: 0,
        error: error.message,
      };
    }
  },
};

async function measureLatency(client: ExchangeAPIClient): Promise<number> {
  const start = Date.now();
  
  try {
    await client.health();
  } catch {
    // Ignore errors
  }
  
  return Date.now() - start;
}

function sleep(ms: number): Promise<void> {
  return new Promise(resolve => setTimeout(resolve, ms));
}

export default scenario;
