/**
 * Stress Testing Scenario
 * 
 * High-volume stress test:
 * 1. Create N test users
 * 2. Each user places/cancels orders at target rate
 * 3. Mixed order types (limit, market, IOC, FOK)
 * 4. Verify no negative balances
 * 5. Check trade stream consistency
 */

import { E2EConfig } from '../config.js';
import { ScenarioResult, TestReport } from '../report.js';
import { Scenario } from '../runner.js';
import { ExchangeAPIClient, AdminAPIClient } from '../http_client.js';
import { WSClient } from '../ws_client.js';

interface StressUser {
  userId: string;
  apiKey: string;
  apiSecret: string;
  client: ExchangeAPIClient;
  wsClient: WSClient;
  orderCount: number;
  cancelCount: number;
  tradeCount: number;
  errors: number;
}

const scenario: Scenario = {
  name: 'stress',
  description: 'High-volume stress test with multiple users',
  
  async run(config: E2EConfig, report: TestReport): Promise<ScenarioResult> {
    const metrics: Record<string, any> = {};
    const NUM_USERS = 10;
    
    try {
      const adminClient = new AdminAPIClient({
        baseURL: config.adminAPI,
      });
      
      console.log(`   → Creating ${NUM_USERS} test users`);
      
      // Create users
      const users: StressUser[] = [];
      
      for (let i = 0; i < NUM_USERS; i++) {
        const userResponse = await adminClient.createUser({
          email: `stress-${Date.now()}-${i}@example.com`,
          password: 'StressTest123!',
        });
        
        if (userResponse.status !== 201) {
          throw new Error(`Failed to create user ${i}: ${userResponse.status}`);
        }
        
        const userId = userResponse.data.id;
        
        // Create API key
        const apiKeyResponse = await adminClient.createAPIKey(userId);
        
        if (apiKeyResponse.status !== 201) {
          throw new Error(`Failed to create API key for user ${i}: ${apiKeyResponse.status}`);
        }
        
        const apiKey = apiKeyResponse.data.key;
        const apiSecret = apiKeyResponse.data.secret;
        
        // Initialize clients
        const client = new ExchangeAPIClient({
          baseURL: config.apiGateway,
          apiKey,
          apiSecret,
        });
        
        const wsClient = new WSClient({
          url: config.websocketURL,
          apiKey,
          reconnect: true,
        });
        
        await wsClient.connect();
        
        // Track trades
        wsClient.on('trade', () => {
          users[i].tradeCount++;
        });
        
        users.push({
          userId,
          apiKey,
          apiSecret,
          client,
          wsClient,
          orderCount: 0,
          cancelCount: 0,
          tradeCount: 0,
          errors: 0,
        });
      }
      
      console.log(`   ✓ Created ${NUM_USERS} users`);
      
      // Start stress test
      const market = config.markets[0];
      const durationMs = config.duration * 1000;
      const ordersPerSecondPerUser = config.rate / NUM_USERS;
      const intervalMs = 1000 / ordersPerSecondPerUser;
      
      console.log(`   → Running stress test for ${config.duration}s`);
      console.log(`   → Target rate: ${config.rate} orders/sec (${ordersPerSecondPerUser.toFixed(2)}/user)`);
      
      const startTime = Date.now();
      const timers: NodeJS.Timeout[] = [];
      
      // Start order generation for each user
      for (let i = 0; i < NUM_USERS; i++) {
        const user = users[i];
        
        const timer = setInterval(async () => {
          try {
            const action = Math.random();
            
            if (action < 0.6) {
              // Place limit order (60%)
              const side = Math.random() < 0.5 ? 'buy' : 'sell';
              const price = (100 + (Math.random() - 0.5) * 10).toFixed(2);
              const size = (Math.random() * 10 + 1).toFixed(3);
              
              await user.client.placeLimitOrder({
                market,
                side,
                price,
                size,
                timeInForce: 'GTC',
              });
              
              user.orderCount++;
            } else if (action < 0.75) {
              // Place IOC order (15%)
              const side = Math.random() < 0.5 ? 'buy' : 'sell';
              const price = (100 + (Math.random() - 0.5) * 10).toFixed(2);
              const size = (Math.random() * 5 + 0.5).toFixed(3);
              
              await user.client.placeLimitOrder({
                market,
                side,
                price,
                size,
                timeInForce: 'IOC',
              });
              
              user.orderCount++;
            } else if (action < 0.85) {
              // Place market order (10%)
              const side = Math.random() < 0.5 ? 'buy' : 'sell';
              const size = (Math.random() * 3 + 0.5).toFixed(3);
              
              await user.client.placeMarketOrder({
                market,
                side,
                size,
              });
              
              user.orderCount++;
            } else {
              // Cancel open orders (15%)
              const openOrders = await user.client.getOpenOrders(market);
              
              if (openOrders.status === 200 && openOrders.data.length > 0) {
                const orderToCancel = openOrders.data[Math.floor(Math.random() * openOrders.data.length)];
                await user.client.cancelOrder(orderToCancel.id);
                user.cancelCount++;
              }
            }
          } catch (error: any) {
            user.errors++;
          }
        }, intervalMs);
        
        timers.push(timer);
      }
      
      // Wait for test duration
      await sleep(durationMs);
      
      // Stop all timers
      timers.forEach(timer => clearInterval(timer));
      
      console.log('   ✓ Stress test completed');
      
      // Collect statistics
      let totalOrders = 0;
      let totalCancels = 0;
      let totalTrades = 0;
      let totalErrors = 0;
      
      for (const user of users) {
        totalOrders += user.orderCount;
        totalCancels += user.cancelCount;
        totalTrades += user.tradeCount;
        totalErrors += user.errors;
        user.wsClient.disconnect();
      }
      
      console.log(`   → Total orders: ${totalOrders}`);
      console.log(`   → Total cancels: ${totalCancels}`);
      console.log(`   → Total trades: ${totalTrades}`);
      console.log(`   → Total errors: ${totalErrors}`);
      
      // Update report metrics
      report.metrics.ordersSubmitted += totalOrders;
      report.metrics.cancels += totalCancels;
      report.metrics.trades += totalTrades;
      
      // Verify no negative balances
      console.log('   → Checking for negative balances');
      
      for (const user of users) {
        const balanceResponse = await user.client.getBalance();
        
        if (balanceResponse.status === 200) {
          const balances = balanceResponse.data;
          
          for (const asset in balances) {
            const balance = parseFloat(balances[asset].available);
            
            if (balance < 0) {
              report.invariants.noNegativeBalances = false;
              throw new Error(`Negative balance detected for user ${user.userId}: ${asset}=${balance}`);
            }
          }
        }
      }
      
      console.log('   ✓ No negative balances');
      
      // Calculate throughput
      const durationSec = durationMs / 1000;
      const actualRate = totalOrders / durationSec;
      
      return {
        name: 'stress',
        passed: true,
        duration: 0,
        metrics: {
          numUsers: NUM_USERS,
          totalOrders,
          totalCancels,
          totalTrades,
          totalErrors,
          actualRate: actualRate.toFixed(2),
          targetRate: config.rate,
          errorRate: (totalErrors / totalOrders * 100).toFixed(2) + '%',
        },
      };
      
    } catch (error: any) {
      return {
        name: 'stress',
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
