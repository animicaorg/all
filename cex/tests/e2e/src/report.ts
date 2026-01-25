/**
 * Report Generation
 * 
 * Generates comprehensive reports in JSON and Markdown formats:
 * - Test execution summary
 * - Performance metrics
 * - Invariant checks
 * - Proof bundle references
 * - Recommendations
 */

import * as fs from 'fs/promises';
import * as path from 'path';
import { E2EConfig } from './config.js';

export interface ScenarioResult {
  name: string;
  passed: boolean;
  duration: number;
  error?: string;
  metrics?: Record<string, any>;
}

export interface Metrics {
  ordersSubmitted: number;
  cancels: number;
  trades: number;
  deposits: number;
  withdrawals: number;
  p50LatencyMs: number;
  p99LatencyMs: number;
  wsDisconnects: number;
  faultsInjected: string[];
}

export interface Invariants {
  ledgerDoubleEntryOk: boolean;
  solvencyOk: boolean;
  noNegativeBalances: boolean;
  noDuplicateCredits: boolean;
  tradeLedgerConsistencyOk: boolean;
}

export interface TestReport {
  runId: string;
  timestamp: string;
  config: E2EConfig;
  scenarios: ScenarioResult[];
  metrics: Metrics;
  invariants: Invariants;
  proofBundlePath?: string;
  logsPath?: string;
  passed: boolean;
  duration: number;
}

/**
 * Generate JSON report
 */
export async function generateJSONReport(
  report: TestReport,
  outputPath: string
): Promise<void> {
  const json = JSON.stringify(report, null, 2);
  await fs.writeFile(outputPath, json, 'utf-8');
  console.log(`JSON report saved to: ${outputPath}`);
}

/**
 * Generate Markdown report
 */
export async function generateMarkdownReport(
  report: TestReport,
  outputPath: string
): Promise<void> {
  const md: string[] = [];
  
  // Header
  md.push('# E2E Test Report');
  md.push('');
  md.push(`**Run ID**: ${report.runId}`);
  md.push(`**Timestamp**: ${report.timestamp}`);
  md.push(`**Duration**: ${formatDuration(report.duration)}`);
  md.push(`**Status**: ${report.passed ? '✅ PASSED' : '❌ FAILED'}`);
  md.push('');
  
  // Configuration
  md.push('## Configuration');
  md.push('');
  md.push('```json');
  md.push(JSON.stringify({
    scenario: report.config.scenario,
    duration: report.config.duration,
    rate: report.config.rate,
    markets: report.config.markets,
    seed: report.config.seed,
    bitgoMode: report.config.bitgoEnv,
    chaosEnabled: report.config.enableChaos,
  }, null, 2));
  md.push('```');
  md.push('');
  
  // Scenarios
  md.push('## Scenarios');
  md.push('');
  md.push('| Scenario | Status | Duration | Notes |');
  md.push('|----------|--------|----------|-------|');
  
  for (const scenario of report.scenarios) {
    const status = scenario.passed ? '✅' : '❌';
    const duration = formatDuration(scenario.duration);
    const notes = scenario.error || (scenario.metrics ? `${Object.keys(scenario.metrics).length} metrics` : '-');
    md.push(`| ${scenario.name} | ${status} | ${duration} | ${notes} |`);
  }
  md.push('');
  
  // Metrics
  md.push('## Performance Metrics');
  md.push('');
  md.push('| Metric | Value |');
  md.push('|--------|-------|');
  md.push(`| Orders Submitted | ${report.metrics.ordersSubmitted.toLocaleString()} |`);
  md.push(`| Cancels | ${report.metrics.cancels.toLocaleString()} |`);
  md.push(`| Trades | ${report.metrics.trades.toLocaleString()} |`);
  md.push(`| Deposits | ${report.metrics.deposits} |`);
  md.push(`| Withdrawals | ${report.metrics.withdrawals} |`);
  md.push(`| P50 Latency | ${report.metrics.p50LatencyMs}ms |`);
  md.push(`| P99 Latency | ${report.metrics.p99LatencyMs}ms |`);
  md.push(`| WS Disconnects | ${report.metrics.wsDisconnects} |`);
  
  if (report.metrics.faultsInjected.length > 0) {
    md.push(`| Faults Injected | ${report.metrics.faultsInjected.length} |`);
  }
  md.push('');
  
  // Invariants
  md.push('## Invariant Checks');
  md.push('');
  md.push('| Invariant | Status |');
  md.push('|-----------|--------|');
  md.push(`| Ledger Double-Entry | ${formatCheck(report.invariants.ledgerDoubleEntryOk)} |`);
  md.push(`| Solvency | ${formatCheck(report.invariants.solvencyOk)} |`);
  md.push(`| No Negative Balances | ${formatCheck(report.invariants.noNegativeBalances)} |`);
  md.push(`| No Duplicate Credits | ${formatCheck(report.invariants.noDuplicateCredits)} |`);
  md.push(`| Trade-Ledger Consistency | ${formatCheck(report.invariants.tradeLedgerConsistencyOk)} |`);
  md.push('');
  
  // Proof Bundle
  if (report.proofBundlePath) {
    md.push('## Reconciliation Proof');
    md.push('');
    md.push(`**Proof Bundle**: \`${report.proofBundlePath}\``);
    md.push('');
    
    try {
      const proofContent = await fs.readFile(report.proofBundlePath, 'utf-8');
      const proof = JSON.parse(proofContent);
      
      if (proof.rootHash) {
        md.push(`**Root Hash**: \`${proof.rootHash}\``);
        md.push('');
      }
    } catch {
      // Proof bundle may not exist yet
    }
  }
  
  // Logs
  if (report.logsPath) {
    md.push('## Logs');
    md.push('');
    md.push(`Service logs saved to: \`${report.logsPath}\``);
    md.push('');
  }
  
  // Recommendations
  md.push('## Recommendations');
  md.push('');
  
  if (!report.passed) {
    md.push('⚠️ **Some tests failed. Review the following:**');
    md.push('');
    
    const failedScenarios = report.scenarios.filter(s => !s.passed);
    for (const scenario of failedScenarios) {
      md.push(`- **${scenario.name}**: ${scenario.error || 'Unknown error'}`);
    }
    md.push('');
  }
  
  if (!report.invariants.ledgerDoubleEntryOk) {
    md.push('❌ **Ledger double-entry invariant failed** - Critical issue requiring immediate investigation');
    md.push('');
  }
  
  if (!report.invariants.solvencyOk) {
    md.push('❌ **Solvency check failed** - Exchange liabilities do not match available funds');
    md.push('');
  }
  
  if (!report.invariants.noNegativeBalances) {
    md.push('❌ **Negative balances detected** - Check ledger service logic');
    md.push('');
  }
  
  if (!report.invariants.noDuplicateCredits) {
    md.push('❌ **Duplicate credits detected** - Review idempotency in deposit/withdrawal handlers');
    md.push('');
  }
  
  if (!report.invariants.tradeLedgerConsistencyOk) {
    md.push('❌ **Trade-ledger consistency failed** - Trades not properly reflected in ledger');
    md.push('');
  }
  
  if (report.passed && Object.values(report.invariants).every(v => v)) {
    md.push('✅ All tests passed and invariants hold. Exchange is operating correctly.');
    md.push('');
  }
  
  // Footer
  md.push('---');
  md.push('');
  md.push(`*Generated at ${new Date().toISOString()}*`);
  
  await fs.writeFile(outputPath, md.join('\n'), 'utf-8');
  console.log(`Markdown report saved to: ${outputPath}`);
}

/**
 * Format duration in human-readable form
 */
function formatDuration(ms: number): string {
  const seconds = Math.floor(ms / 1000);
  const minutes = Math.floor(seconds / 60);
  const hours = Math.floor(minutes / 60);
  
  if (hours > 0) {
    return `${hours}h ${minutes % 60}m ${seconds % 60}s`;
  } else if (minutes > 0) {
    return `${minutes}m ${seconds % 60}s`;
  } else {
    return `${seconds}s`;
  }
}

/**
 * Format boolean check
 */
function formatCheck(value: boolean): string {
  return value ? '✅ Pass' : '❌ Fail';
}

/**
 * Print report summary to console
 */
export function printReportSummary(report: TestReport): void {
  console.log('');
  console.log('═══════════════════════════════════════════════════════════════');
  console.log('  E2E Test Report Summary');
  console.log('═══════════════════════════════════════════════════════════════');
  console.log(`  Status:           ${report.passed ? '✅ PASSED' : '❌ FAILED'}`);
  console.log(`  Duration:         ${formatDuration(report.duration)}`);
  console.log(`  Scenarios:        ${report.scenarios.filter(s => s.passed).length}/${report.scenarios.length} passed`);
  console.log(`  Orders:           ${report.metrics.ordersSubmitted.toLocaleString()}`);
  console.log(`  Trades:           ${report.metrics.trades.toLocaleString()}`);
  console.log(`  P99 Latency:      ${report.metrics.p99LatencyMs}ms`);
  console.log('');
  console.log('  Invariants:');
  console.log(`    Double-Entry:   ${formatCheck(report.invariants.ledgerDoubleEntryOk)}`);
  console.log(`    Solvency:       ${formatCheck(report.invariants.solvencyOk)}`);
  console.log(`    No Negative:    ${formatCheck(report.invariants.noNegativeBalances)}`);
  console.log(`    No Duplicates:  ${formatCheck(report.invariants.noDuplicateCredits)}`);
  console.log(`    Trade-Ledger:   ${formatCheck(report.invariants.tradeLedgerConsistencyOk)}`);
  console.log('═══════════════════════════════════════════════════════════════');
  console.log('');
  
  if (report.proofBundlePath) {
    console.log(`  📦 Proof bundle: ${report.proofBundlePath}`);
  }
  
  if (report.logsPath) {
    console.log(`  📋 Logs: ${report.logsPath}`);
  }
  
  console.log('');
}

/**
 * Create initial report structure
 */
export function createReport(config: E2EConfig, runId: string): TestReport {
  return {
    runId,
    timestamp: new Date().toISOString(),
    config,
    scenarios: [],
    metrics: {
      ordersSubmitted: 0,
      cancels: 0,
      trades: 0,
      deposits: 0,
      withdrawals: 0,
      p50LatencyMs: 0,
      p99LatencyMs: 0,
      wsDisconnects: 0,
      faultsInjected: [],
    },
    invariants: {
      ledgerDoubleEntryOk: true,
      solvencyOk: true,
      noNegativeBalances: true,
      noDuplicateCredits: true,
      tradeLedgerConsistencyOk: true,
    },
    passed: true,
    duration: 0,
  };
}
