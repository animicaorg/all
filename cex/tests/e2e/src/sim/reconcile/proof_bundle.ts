/**
 * Proof Bundle Generation
 * 
 * Generates comprehensive proof bundle for audit:
 * - Ledger snapshot
 * - Event hashchain
 * - Invariant results
 * - Attestations
 */

import * as fs from 'fs/promises';
import * as path from 'path';
import * as crypto from 'crypto';
import { LedgerSnapshot, exportSnapshot } from './ledger_snapshot.js';
import { Hashchain, exportHashchain, computeMerkleRoot } from './event_hashchain.js';
import { InvariantReport } from './invariants.js';

export interface ProofBundle {
  version: string;
  timestamp: string;
  bundleHash: string;
  
  snapshot: LedgerSnapshot;
  hashchain: Hashchain;
  invariants: InvariantReport;
  
  attestations: {
    snapshotHash: string;
    hashchainHead: string;
    merkleRoot: string;
    invariantsSummary: {
      passed: number;
      failed: number;
      total: number;
    };
  };
  
  metadata: {
    generatedBy: string;
    environment: string;
    notes?: string;
  };
}

/**
 * Generate proof bundle
 */
export async function generateProofBundle(params: {
  snapshot: LedgerSnapshot;
  hashchain: Hashchain;
  invariants: InvariantReport;
  metadata?: {
    generatedBy?: string;
    environment?: string;
    notes?: string;
  };
}): Promise<ProofBundle> {
  console.log(`[Proof Bundle] Generating proof bundle...`);
  
  const { snapshot, hashchain, invariants, metadata = {} } = params;
  
  // Compute attestations
  const snapshotHash = snapshot.snapshotHash;
  const hashchainHead = hashchain.headHash;
  const merkleRoot = computeMerkleRoot(hashchain);
  
  const bundle: ProofBundle = {
    version: '1.0.0',
    timestamp: new Date().toISOString(),
    bundleHash: '', // Will be computed below
    
    snapshot,
    hashchain,
    invariants,
    
    attestations: {
      snapshotHash,
      hashchainHead,
      merkleRoot,
      invariantsSummary: invariants.summary,
    },
    
    metadata: {
      generatedBy: metadata.generatedBy || 'e2e-test-harness',
      environment: metadata.environment || 'test',
      notes: metadata.notes,
    },
  };
  
  // Compute bundle hash (excluding the hash field itself)
  bundle.bundleHash = computeBundleHash(bundle);
  
  console.log(`[Proof Bundle] Bundle generated`);
  console.log(`[Proof Bundle] Hash: ${bundle.bundleHash}`);
  console.log(`[Proof Bundle] Snapshot: ${snapshotHash}`);
  console.log(`[Proof Bundle] Hashchain: ${hashchainHead}`);
  console.log(`[Proof Bundle] Merkle: ${merkleRoot}`);
  console.log(`[Proof Bundle] Invariants: ${invariants.summary.passed}/${invariants.summary.total} passed`);
  
  return bundle;
}

/**
 * Save proof bundle to disk
 */
export async function saveProofBundle(
  bundle: ProofBundle,
  outputDir: string
): Promise<{
  bundlePath: string;
  snapshotPath: string;
  hashchainPath: string;
  invariantsPath: string;
}> {
  console.log(`[Proof Bundle] Saving to ${outputDir}...`);
  
  // Create output directory
  await fs.mkdir(outputDir, { recursive: true });
  
  const timestamp = bundle.timestamp.replace(/[:.]/g, '-');
  
  // Save full bundle
  const bundlePath = path.join(outputDir, `proof_bundle_${timestamp}.json`);
  await fs.writeFile(bundlePath, JSON.stringify(bundle, null, 2));
  
  // Save individual components for easier inspection
  const snapshotPath = path.join(outputDir, `snapshot_${timestamp}.json`);
  await fs.writeFile(snapshotPath, exportSnapshot(bundle.snapshot));
  
  const hashchainPath = path.join(outputDir, `hashchain_${timestamp}.json`);
  await fs.writeFile(hashchainPath, exportHashchain(bundle.hashchain));
  
  const invariantsPath = path.join(outputDir, `invariants_${timestamp}.json`);
  await fs.writeFile(invariantsPath, JSON.stringify(bundle.invariants, null, 2));
  
  // Save summary
  const summaryPath = path.join(outputDir, `summary_${timestamp}.txt`);
  const summary = generateSummaryText(bundle);
  await fs.writeFile(summaryPath, summary);
  
  console.log(`[Proof Bundle] Saved:`);
  console.log(`  Bundle:     ${bundlePath}`);
  console.log(`  Snapshot:   ${snapshotPath}`);
  console.log(`  Hashchain:  ${hashchainPath}`);
  console.log(`  Invariants: ${invariantsPath}`);
  console.log(`  Summary:    ${summaryPath}`);
  
  return {
    bundlePath,
    snapshotPath,
    hashchainPath,
    invariantsPath,
  };
}

/**
 * Load proof bundle from disk
 */
export async function loadProofBundle(bundlePath: string): Promise<ProofBundle> {
  const content = await fs.readFile(bundlePath, 'utf-8');
  return JSON.parse(content);
}

/**
 * Verify proof bundle integrity
 */
export function verifyProofBundle(bundle: ProofBundle): {
  valid: boolean;
  errors: string[];
} {
  console.log(`[Proof Bundle] Verifying bundle integrity...`);
  
  const errors: string[] = [];
  
  // Verify bundle hash
  const expectedHash = computeBundleHash(bundle);
  if (bundle.bundleHash !== expectedHash) {
    errors.push(`Bundle hash mismatch (expected ${expectedHash}, got ${bundle.bundleHash})`);
  }
  
  // Verify snapshot hash
  if (bundle.attestations.snapshotHash !== bundle.snapshot.snapshotHash) {
    errors.push('Snapshot hash mismatch');
  }
  
  // Verify hashchain head
  if (bundle.attestations.hashchainHead !== bundle.hashchain.headHash) {
    errors.push('Hashchain head mismatch');
  }
  
  // Verify merkle root
  const calculatedMerkle = computeMerkleRoot(bundle.hashchain);
  if (bundle.attestations.merkleRoot !== calculatedMerkle) {
    errors.push('Merkle root mismatch');
  }
  
  // Verify invariants summary
  const summary = bundle.invariants.summary;
  if (summary.passed + summary.failed !== summary.total) {
    errors.push('Invariants summary count mismatch');
  }
  
  const valid = errors.length === 0;
  
  if (valid) {
    console.log(`[Proof Bundle] ✓ Bundle is valid`);
  } else {
    console.log(`[Proof Bundle] ✗ Bundle is invalid`);
    errors.forEach(err => console.log(`  - ${err}`));
  }
  
  return { valid, errors };
}

/**
 * Compute bundle hash
 */
function computeBundleHash(bundle: ProofBundle): string {
  const canonical = JSON.stringify({
    version: bundle.version,
    timestamp: bundle.timestamp,
    attestations: bundle.attestations,
    metadata: bundle.metadata,
  });
  
  return crypto.createHash('sha256').update(canonical).digest('hex');
}

/**
 * Generate human-readable summary
 */
function generateSummaryText(bundle: ProofBundle): string {
  const lines: string[] = [];
  
  lines.push('='.repeat(60));
  lines.push('PROOF BUNDLE SUMMARY');
  lines.push('='.repeat(60));
  lines.push('');
  
  lines.push(`Generated: ${bundle.timestamp}`);
  lines.push(`Version:   ${bundle.version}`);
  lines.push(`Hash:      ${bundle.bundleHash}`);
  lines.push('');
  
  lines.push('ATTESTATIONS:');
  lines.push(`  Snapshot Hash:   ${bundle.attestations.snapshotHash}`);
  lines.push(`  Hashchain Head:  ${bundle.attestations.hashchainHead}`);
  lines.push(`  Merkle Root:     ${bundle.attestations.merkleRoot}`);
  lines.push('');
  
  lines.push('LEDGER SNAPSHOT:');
  lines.push(`  Entries:         ${bundle.snapshot.entryCount}`);
  lines.push(`  Users:           ${bundle.snapshot.balancesByUser.size}`);
  lines.push(`  Assets:          ${bundle.snapshot.totalsByAsset.size}`);
  lines.push('');
  
  lines.push('EVENT HASHCHAIN:');
  lines.push(`  Length:          ${bundle.hashchain.length}`);
  lines.push(`  Start Time:      ${bundle.hashchain.startTime}`);
  lines.push(`  End Time:        ${bundle.hashchain.endTime}`);
  lines.push('');
  
  lines.push('INVARIANTS:');
  const inv = bundle.invariants.summary;
  lines.push(`  Total Checks:    ${inv.total}`);
  lines.push(`  Passed:          ${inv.passed}`);
  lines.push(`  Failed:          ${inv.failed}`);
  lines.push(`  Status:          ${bundle.invariants.allPassed ? '✓ PASS' : '✗ FAIL'}`);
  lines.push('');
  
  if (!bundle.invariants.allPassed) {
    lines.push('FAILED INVARIANTS:');
    bundle.invariants.results
      .filter(r => !r.passed)
      .forEach(r => {
        lines.push(`  ✗ ${r.name}: ${r.message}`);
      });
    lines.push('');
  }
  
  lines.push('METADATA:');
  lines.push(`  Generated By:    ${bundle.metadata.generatedBy}`);
  lines.push(`  Environment:     ${bundle.metadata.environment}`);
  if (bundle.metadata.notes) {
    lines.push(`  Notes:           ${bundle.metadata.notes}`);
  }
  lines.push('');
  
  lines.push('='.repeat(60));
  
  return lines.join('\n');
}

/**
 * Compare two proof bundles
 */
export function compareBundles(
  bundle1: ProofBundle,
  bundle2: ProofBundle
): {
  identical: boolean;
  differences: string[];
} {
  const differences: string[] = [];
  
  // Compare snapshots
  if (bundle1.snapshot.snapshotHash !== bundle2.snapshot.snapshotHash) {
    differences.push('Snapshot hash differs');
  }
  
  if (bundle1.snapshot.entryCount !== bundle2.snapshot.entryCount) {
    differences.push(`Entry count differs: ${bundle1.snapshot.entryCount} vs ${bundle2.snapshot.entryCount}`);
  }
  
  // Compare hashchains
  if (bundle1.hashchain.headHash !== bundle2.hashchain.headHash) {
    differences.push('Hashchain head differs');
  }
  
  if (bundle1.hashchain.length !== bundle2.hashchain.length) {
    differences.push(`Hashchain length differs: ${bundle1.hashchain.length} vs ${bundle2.hashchain.length}`);
  }
  
  // Compare invariants
  if (bundle1.invariants.allPassed !== bundle2.invariants.allPassed) {
    differences.push('Invariant pass status differs');
  }
  
  return {
    identical: differences.length === 0,
    differences,
  };
}
