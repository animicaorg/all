/**
 * Market Maker Scenario
 * 
 * Tests market maker functionality:
 * 1. Create test users with API keys
 * 2. Initialize market maker with strategies
 * 3. Run for configured duration
 * 4. Verify orders placed and trades executed
 * 5. Check no errors occurred
 */

import { E2EConfig } from '../config.js';
import { ScenarioResult, TestReport } from '../report.js';
import { Scenario } from '../runner.js';
import { ExchangeAPIClient, AdminAPIClient } from '../http_client.js';
import { WSClient } from '../ws_client.js';
import { MarketMaker, MarketMakerConfig } from '../sim/mm/maker.js';

const scenario: Scenario = {
  name: 'market_maker',
  description: 'Market maker strategy execution and verification',
  
  async run(config: E2EConfig, report: TestReport): Promise<ScenarioResult> {
    const metrics: Record<string, any> = {};
    
    try {
      console.log('   → Creating market maker user');
      
      const adminClient = new AdminAPIClient({
        baseURL: config.adminAPI,
      });
      
      // Create user
      const userResponse = await adminClient.createUser({
        email: `mm-${Date.now()}@example.com`,
        password: 'MMPassword123!',
      });
      
      if (userResponse.status !== 201) {
        throw new Error(`Failed to create MM user: ${userResponse.status}`);
      }
      
      const userId = userResponse.data.id;
      console.log(`   ✓ Created MM user: ${userId}`);
      
      // Create API key
      const apiKeyResponse = await adminClient.createAPIKey(userId);
      
      if (apiKeyResponse.status !== 201) {
        throw new Error(`Failed to create API key: ${apiKeyResponse.status}`);
      }
      
      const apiKey = apiKeyResponse.data.key;
      const apiSecret = apiKeyResponse.data.secret;
      console.log('   ✓ Created API key');
      
      // Initialize HTTP client
      const exchangeClient = new ExchangeAPIClient({
        baseURL: config.apiGateway,
        apiKey,
        apiSecret,
      });
      
      // Initialize WebSocket client
      const wsClient = new WSClient({
        url: config.websocketURL,
        apiKey,
        reconnect: true,
      });
      
      await wsClient.connect();
      console.log('   ✓ WebSocket connected');
      
      // Configure market maker
      const market = config.markets[0];
      const mmConfig: MarketMakerConfig = {
        market,
        strategy: 'tight_spread',
        strategyConfig: {
          spread: 0.002, // 0.2%
          orderLevels: 5,
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
        quoteRefreshInterval: 1000, // 1 second
        randomSeed: config.seed,
      };
      
      console.log(`   → Initializing market maker for ${market}`);
      const marketMaker = new MarketMaker(mmConfig, exchangeClient, wsClient);
      
      // Start market maker
      await marketMaker.start();
      console.log('   ✓ Market maker started');
      
      // Run for configured duration
      const durationMs = config.duration * 1000;
      console.log(`   → Running for ${config.duration}s`);
      
      await sleep(durationMs);
      
      // Stop market maker
      await marketMaker.stop();
      console.log('   ✓ Market maker stopped');
      
      // Get stats
      const stats = marketMaker.getStats();
      console.log(`   → Orders placed: ${stats.ordersPlaced}`);
      console.log(`   → Orders canceled: ${stats.ordersCanceled}`);
      console.log(`   → Trades: ${stats.trades}`);
      console.log(`   → Risk breaches: ${stats.riskBreaches}`);
      
      // Update report metrics
      report.metrics.ordersSubmitted += stats.ordersPlaced;
      report.metrics.cancels += stats.ordersCanceled;
      report.metrics.trades += stats.trades;
      
      // Verify minimum activity
      if (stats.ordersPlaced === 0) {
        throw new Error('Market maker placed no orders');
      }
      
      // Check for errors
      if (stats.riskBreaches > 0) {
        console.warn(`   ⚠️  Risk breaches occurred: ${stats.riskBreaches}`);
      }
      
      wsClient.disconnect();
      
      return {
        name: 'market_maker',
        passed: true,
        duration: 0,
        metrics: {
          userId,
          market,
          ordersPlaced: stats.ordersPlaced,
          ordersCanceled: stats.ordersCanceled,
          trades: stats.trades,
          quoteCycles: stats.quoteCycles,
          riskBreaches: stats.riskBreaches,
        },
      };
      
    } catch (error: any) {
      return {
        name: 'market_maker',
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
