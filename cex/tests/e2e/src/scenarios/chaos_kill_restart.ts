/**
 * Service Restart Chaos Scenario
 * 
 * Tests system resilience to service failures:
 * 1. Use DockerChaos
 * 2. Kill random services
 * 3. Verify recovery
 * 4. Check no duplicate operations
 */

import { E2EConfig } from '../config.js';
import { ScenarioResult, TestReport } from '../report.js';
import { Scenario } from '../runner.js';
import { ExchangeAPIClient, AdminAPIClient } from '../http_client.js';
import { DockerChaos, DockerChaosConfig } from '../sim/chaos/docker.js';

const scenario: Scenario = {
  name: 'chaos_kill_restart',
  description: 'Service restart chaos with recovery verification',
  
  async run(config: E2EConfig, report: TestReport): Promise<ScenarioResult> {
    if (!config.enableChaos) {
      return {
        name: 'chaos_kill_restart',
        passed: true,
        duration: 0,
        error: 'Chaos testing disabled (use --chaos flag)',
      };
    }
    
    try {
      console.log('   → Initializing Docker chaos orchestrator');
      
      const chaosConfig: DockerChaosConfig = {
        socketPath: '/var/run/docker.sock',
      };
      
      const chaos = new DockerChaos(chaosConfig);
      
      // List CEX containers
      console.log('   → Listing CEX services');
      const containers = await chaos.listContainers({
        label: 'com.animica.cex=true',
      });
      
      if (containers.length === 0) {
        throw new Error('No CEX containers found (ensure docker-compose labels are set)');
      }
      
      console.log(`   ✓ Found ${containers.length} services`);
      
      // Select services to kill (excluding critical ones)
      const killableServices = containers.filter(c => {
        const name = c.name.toLowerCase();
        return !name.includes('postgres') && 
               !name.includes('redis') && 
               !name.includes('nats');
      });
      
      if (killableServices.length === 0) {
        throw new Error('No killable services found');
      }
      
      console.log(`   → Killable services: ${killableServices.map(c => c.name).join(', ')}`);
      
      // Setup test user
      const adminClient = new AdminAPIClient({
        baseURL: config.adminAPI,
      });
      
      const userResponse = await adminClient.createUser({
        email: `chaos-${Date.now()}@example.com`,
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
      
      // Inject chaos events
      const chaosEvents: string[] = [];
      const numEvents = 3;
      
      for (let i = 0; i < numEvents; i++) {
        // Select random service
        const target = killableServices[Math.floor(Math.random() * killableServices.length)];
        
        console.log(`   → Chaos event ${i + 1}: Killing ${target.name}`);
        
        // Kill service
        await chaos.killContainer(target.id);
        chaosEvents.push(`kill:${target.name}`);
        
        console.log(`   ✓ ${target.name} killed`);
        
        // Wait a bit
        await sleep(2000);
        
        // Try to place an order during chaos
        try {
          await exchangeClient.placeLimitOrder({
            market: config.markets[0],
            side: 'buy',
            price: '100.00',
            size: '1.0',
            timeInForce: 'GTC',
          });
          
          console.log('   → Order placed during chaos');
        } catch (error: any) {
          console.log(`   → Expected error during chaos: ${error.message}`);
        }
        
        // Restart service
        console.log(`   → Restarting ${target.name}`);
        await chaos.restartContainer(target.id);
        chaosEvents.push(`restart:${target.name}`);
        
        // Wait for recovery
        await sleep(5000);
        
        console.log(`   ✓ ${target.name} restarted`);
      }
      
      // Verify system recovery
      console.log('   → Verifying system recovery');
      
      let recovered = false;
      for (let attempt = 0; attempt < 10; attempt++) {
        try {
          const healthResponse = await exchangeClient.health();
          
          if (healthResponse) {
            recovered = true;
            break;
          }
        } catch {
          await sleep(2000);
        }
      }
      
      if (!recovered) {
        throw new Error('System failed to recover after chaos');
      }
      
      console.log('   ✓ System recovered');
      
      // Place test order after recovery
      console.log('   → Placing test order after recovery');
      
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
      
      console.log('   ✓ Order placed successfully');
      
      // Check for duplicate operations
      console.log('   → Checking for duplicate operations');
      
      const openOrders = await exchangeClient.getOpenOrders();
      
      if (openOrders.status !== 200) {
        throw new Error('Failed to get open orders');
      }
      
      const orderIds = new Set();
      const duplicates: string[] = [];
      
      for (const order of openOrders.data) {
        if (orderIds.has(order.id)) {
          duplicates.push(order.id);
        }
        orderIds.add(order.id);
      }
      
      if (duplicates.length > 0) {
        report.invariants.noDuplicateCredits = false;
        throw new Error(`Duplicate orders detected: ${duplicates.join(', ')}`);
      }
      
      console.log('   ✓ No duplicates found');
      
      // Update report metrics
      report.metrics.faultsInjected.push(...chaosEvents);
      
      return {
        name: 'chaos_kill_restart',
        passed: true,
        duration: 0,
        metrics: {
          userId,
          chaosEvents,
          numEvents,
          servicesAffected: killableServices.length,
          recovered: true,
        },
      };
      
    } catch (error: any) {
      return {
        name: 'chaos_kill_restart',
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
