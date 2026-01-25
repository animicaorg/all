/**
 * E2E Test Simulators Index
 * 
 * Aggregates all simulator modules for easy importing.
 */

// Market Maker Simulators
export * from './mm/inventory.js';
export * from './mm/quoting.js';
export * from './mm/strategies.js';
export * from './mm/risk.js';
export * from './mm/maker.js';

// Deposit Simulators
export * from './deposits/bitgo_mock.js';
export { 
  BitGoSandbox, 
  createSandboxDeposit,
  type BitGoSandboxConfig as DepositBitGoSandboxConfig,
  type DepositAddress 
} from './deposits/bitgo_sandbox.js';
export * from './deposits/animica_devnet.js';
export * from './deposits/animica_reorg.js';

// Withdrawal Simulators
export * from './withdrawals/bitgo_mock.js';
export { 
  BitGoSandboxWithdrawal, 
  executeBatchWithdrawals,
  type BitGoSandboxConfig as WithdrawalBitGoSandboxConfig,
  type WithdrawalParams,
  type WithdrawalResult 
} from './withdrawals/bitgo_sandbox.js';
export { 
  AnimicaWithdrawalClient,
  simulateBatchAnimicaWithdrawals,
  testWithdrawalFlow,
  type AnimicaWithdrawalConfig,
  type AnimicaWithdrawalParams,
  type AnimicaWithdrawalResult
} from './withdrawals/animica_devnet.js';

// Chaos Testing
export * from './chaos/docker.js';
export * from './chaos/toxiproxy.js';
export * from './chaos/faults.js';

// Reconciliation
export * from './reconcile/ledger_snapshot.js';
export * from './reconcile/event_hashchain.js';
export * from './reconcile/invariants.js';
export * from './reconcile/proof_bundle.js';
