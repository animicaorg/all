#!/usr/bin/env node
/**
 * E2E Test Runner
 * 
 * Main orchestrator for E2E test execution:
 * 1. Load configuration
 * 2. Initialize infrastructure
 * 3. Run scenarios
 * 4. Generate reports
 * 5. Clean up
 */

import { randomUUID } from 'crypto';
import { loadConfig, validateConfig, printConfig, E2EConfig } from './config.js';
import {
  createReport,
  generateJSONReport,
  generateMarkdownReport,
  printReportSummary,
  TestReport,
  ScenarioResult,
} from './report.js';

/**
 * Scenario interface
 */
export interface Scenario {
  name: string;
  description: string;
  run(config: E2EConfig, report: TestReport): Promise<ScenarioResult>;
}

/**
 * Main runner
 */
async function main() {
  const startTime = Date.now();
  
  console.log('');
  console.log('╔═══════════════════════════════════════════════════════════════╗');
  console.log('║                 CEX E2E Test Harness                          ║');
  console.log('╚═══════════════════════════════════════════════════════════════╝');
  console.log('');
  
  // Load and validate config
  const config = loadConfig();
  
  try {
    validateConfig(config);
  } catch (error: any) {
    console.error('❌ Configuration error:', error.message);
    process.exit(1);
  }
  
  printConfig(config);
  
  // Create run ID
  const runId = randomUUID();
  const report = createReport(config, runId);
  
  try {
    // Initialize infrastructure
    console.log('🚀 Initializing infrastructure...');
    await initializeInfrastructure(config);
    console.log('✅ Infrastructure ready\n');
    
    // Load scenarios
    const scenarios = await loadScenarios(config);
    console.log(`📋 Loaded ${scenarios.length} scenario(s)\n`);
    
    // Run scenarios
    for (const scenario of scenarios) {
      console.log(`\n▶️  Running scenario: ${scenario.name}`);
      console.log(`   ${scenario.description}`);
      console.log('');
      
      const scenarioStartTime = Date.now();
      
      try {
        const result = await scenario.run(config, report);
        result.duration = Date.now() - scenarioStartTime;
        report.scenarios.push(result);
        
        if (result.passed) {
          console.log(`✅ ${scenario.name} passed (${formatDuration(result.duration)})`);
        } else {
          console.log(`❌ ${scenario.name} failed: ${result.error}`);
          report.passed = false;
        }
      } catch (error: any) {
        const result: ScenarioResult = {
          name: scenario.name,
          passed: false,
          duration: Date.now() - scenarioStartTime,
          error: error.message || 'Unknown error',
        };
        report.scenarios.push(result);
        report.passed = false;
        console.log(`❌ ${scenario.name} failed: ${error.message}`);
      }
    }
    
    // Calculate total duration
    report.duration = Date.now() - startTime;
    
    // Generate reports
    console.log('\n📊 Generating reports...');
    await generateJSONReport(report, config.reportJSON);
    await generateMarkdownReport(report, config.reportMarkdown);
    console.log('');
    
    // Print summary
    printReportSummary(report);
    
    // Exit with appropriate code
    process.exit(report.passed ? 0 : 1);
    
  } catch (error: any) {
    console.error('❌ Fatal error:', error.message);
    console.error(error.stack);
    
    report.duration = Date.now() - startTime;
    report.passed = false;
    
    // Try to save reports even on fatal error
    try {
      await generateJSONReport(report, config.reportJSON);
      await generateMarkdownReport(report, config.reportMarkdown);
    } catch {
      // Ignore report generation errors
    }
    
    process.exit(1);
  } finally {
    // Cleanup if needed
    if (!config.keepStackRunning) {
      console.log('🧹 Cleaning up...');
      await cleanup(config);
    } else {
      console.log('⚠️  Stack kept running (--keep flag)');
    }
  }
}

/**
 * Initialize infrastructure
 */
async function initializeInfrastructure(config: E2EConfig): Promise<void> {
  // Wait for services to be healthy
  const services = [
    { name: 'API Gateway', url: `${config.apiGateway}/health` },
    { name: 'Admin API', url: `${config.adminAPI}/health` },
  ];
  
  for (const service of services) {
    await waitForService(service.name, service.url);
  }
}

/**
 * Wait for service to be healthy
 */
async function waitForService(name: string, url: string, timeout = 30000): Promise<void> {
  const startTime = Date.now();
  
  while (Date.now() - startTime < timeout) {
    try {
      const response = await fetch(url, {
        signal: AbortSignal.timeout(5000),
      });
      
      if (response.ok) {
        console.log(`   ✓ ${name} is healthy`);
        return;
      }
    } catch {
      // Service not ready yet
    }
    
    await new Promise(resolve => setTimeout(resolve, 1000));
  }
  
  throw new Error(`Timeout waiting for ${name} to become healthy`);
}

/**
 * Load scenarios based on config
 */
async function loadScenarios(config: E2EConfig): Promise<Scenario[]> {
  const scenarios: Scenario[] = [];
  
  // Import scenario modules (will be implemented next)
  const scenarioModules: Record<string, string> = {
    'smoke': './scenarios/smoke.js',
    'market_maker': './scenarios/market_maker.js',
    'stress': './scenarios/stress.js',
    'deposits_bitgo': './scenarios/deposits_bitgo.js',
    'deposits_animica': './scenarios/deposits_animica.js',
    'withdrawals_bitgo': './scenarios/withdrawals_bitgo.js',
    'withdrawals_animica': './scenarios/withdrawals_animica.js',
    'chaos_kill_restart': './scenarios/chaos_kill_restart.js',
    'chaos_partition': './scenarios/chaos_partition.js',
    'reorg_animica': './scenarios/reorg_animica.js',
    'reconciliation_proof': './scenarios/reconciliation_proof.js',
  };
  
  if (config.scenario === 'all') {
    // Run all scenarios
    for (const [name, modulePath] of Object.entries(scenarioModules)) {
      try {
        const module = await import(modulePath);
        scenarios.push(module.default);
      } catch (error) {
        console.warn(`   ⚠️  Failed to load scenario ${name}: ${(error as Error).message}`);
      }
    }
  } else {
    // Run specific scenario
    const modulePath = scenarioModules[config.scenario];
    
    if (!modulePath) {
      throw new Error(`Unknown scenario: ${config.scenario}`);
    }
    
    try {
      const module = await import(modulePath);
      scenarios.push(module.default);
    } catch (error) {
      throw new Error(`Failed to load scenario ${config.scenario}: ${(error as Error).message}`);
    }
  }
  
  return scenarios;
}

/**
 * Cleanup resources
 */
async function cleanup(config: E2EConfig): Promise<void> {
  // Placeholder for cleanup logic
  // Will be implemented when docker-compose integration is added
}

/**
 * Format duration
 */
function formatDuration(ms: number): string {
  const seconds = Math.floor(ms / 1000);
  const minutes = Math.floor(seconds / 60);
  
  if (minutes > 0) {
    return `${minutes}m ${seconds % 60}s`;
  } else {
    return `${seconds}s`;
  }
}

/**
 * Run the test harness
 */
main().catch((error) => {
  console.error('Unhandled error:', error);
  process.exit(1);
});
