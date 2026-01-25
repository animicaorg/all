/**
 * Smoke Test Scenario
 * 
 * Quick validation that all services are operational:
 * - Service health checks
 * - Basic API operations
 * - Market data access
 * - WebSocket connectivity
 */

import { E2EConfig } from '../config.js';
import { ScenarioResult, TestReport } from '../report.js';
import { Scenario } from '../runner.js';
import { ExchangeAPIClient, AdminAPIClient } from '../http_client.js';
import { WSClient } from '../ws_client.js';

const scenario: Scenario = {
  name: 'smoke',
  description: 'Basic health checks and service validation',
  
  async run(config: E2EConfig, report: TestReport): Promise<ScenarioResult> {
    const metrics: Record<string, any> = {};
    
    try {
      // 1. Check API Gateway health
      const exchangeClient = new ExchangeAPIClient({
        baseURL: config.apiGateway,
      });
      
      const healthCheck = await exchangeClient.health();
      if (!healthCheck) {
        throw new Error('API Gateway health check failed');
      }
      
      console.log('   ✓ API Gateway is healthy');
      
      // 2. Check Admin API health
      const adminClient = new AdminAPIClient({
        baseURL: config.adminAPI,
      });
      
      const adminHealth = await adminClient.health();
      if (!adminHealth) {
        throw new Error('Admin API health check failed');
      }
      
      console.log('   ✓ Admin API is healthy');
      
      // 3. Create test user
      const userResponse = await adminClient.createUser({
        email: `test-${Date.now()}@example.com`,
        password: 'TestPassword123!',
      });
      
      if (userResponse.status !== 201) {
        throw new Error(`Failed to create test user: ${userResponse.status}`);
      }
      
      const userId = userResponse.data.id;
      console.log(`   ✓ Created test user: ${userId}`);
      
      // 4. Create API key
      const apiKeyResponse = await adminClient.createAPIKey(userId);
      
      if (apiKeyResponse.status !== 201) {
        throw new Error(`Failed to create API key: ${apiKeyResponse.status}`);
      }
      
      const apiKey = apiKeyResponse.data.key;
      const apiSecret = apiKeyResponse.data.secret;
      console.log('   ✓ Created API key');
      
      // 5. Authenticate with exchange API
      exchangeClient.setAuth(apiKey, apiSecret);
      
      // 6. Get balance
      const balanceResponse = await exchangeClient.getBalance();
      
      if (balanceResponse.status !== 200) {
        throw new Error(`Failed to get balance: ${balanceResponse.status}`);
      }
      
      console.log('   ✓ Retrieved account balance');
      
      // 7. Get market data
      const market = config.markets[0];
      const orderbookResponse = await exchangeClient.getOrderbook(market);
      
      if (orderbookResponse.status !== 200) {
        throw new Error(`Failed to get orderbook: ${orderbookResponse.status}`);
      }
      
      console.log(`   ✓ Retrieved orderbook for ${market}`);
      
      // 8. Test WebSocket connection
      const wsClient = new WSClient({
        url: config.websocketURL,
        apiKey,
        reconnect: false,
      });
      
      await wsClient.connect();
      console.log('   ✓ WebSocket connected');
      
      // 9. Subscribe to orderbook
      wsClient.subscribeOrderbook(market);
      
      // Wait for orderbook message
      try {
        await wsClient.waitFor('orderbook', 5000);
        console.log('   ✓ Received orderbook update via WebSocket');
      } catch {
        console.warn('   ⚠️  No orderbook update received (may be empty)');
      }
      
      wsClient.disconnect();
      
      // All checks passed
      return {
        name: 'smoke',
        passed: true,
        duration: 0, // Will be set by runner
        metrics: {
          userId,
          market,
          apiKeyCreated: true,
          wsConnected: true,
        },
      };
      
    } catch (error: any) {
      return {
        name: 'smoke',
        passed: false,
        duration: 0,
        error: error.message,
      };
    }
  },
};

export default scenario;
