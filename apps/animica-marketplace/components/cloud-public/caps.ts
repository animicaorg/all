// Human-readable vocabulary for Python Cloud capabilities, shown to a USER before they
// authorize an app. Mirrors CAPABILITIES / SENSITIVE_CAPABILITIES in lib/cloud/config.ts —
// duplicated here (not imported) because that module reads process.env and must never reach
// a 'use client' bundle. The server enforces the real list; this file only labels it.

export interface CapabilityInfo {
  key: string;
  label: string;
  icon: string;
  desc: string;
  sensitive: boolean; // sensitive => requires an explicit, revocable user grant before use
}

export const CAPABILITY_INFO: CapabilityInfo[] = [
  { key: 'AI_INFERENCE', label: 'AI inference', icon: '🧠', sensitive: false,
    desc: 'Call Animica AI models. Token usage is metered and billed as part of each execution.' },
  { key: 'CALL_FUNCTION', label: 'Call other functions', icon: '🔗', sensitive: true,
    desc: 'Invoke other deployed Python Cloud functions from inside this one. Each nested call is metered.' },
  { key: 'CALL_APP', label: 'Call other apps', icon: '📦', sensitive: true,
    desc: 'Invoke other marketplace apps on your behalf. Each nested call is metered and may cost ANM.' },
  { key: 'READ_CHAIN', label: 'Read the Animica chain', icon: '⛓️', sensitive: false,
    desc: 'Read public on-chain data: heads, blocks, balances, transactions.' },
  { key: 'SPEND_ANM', label: 'Spend ANM', icon: '💸', sensitive: true,
    desc: 'Spend ANM from your balance, bounded by per-call, per-execution and daily caps that you set and can revoke at any time.' },
  { key: 'PERSIST_STATE', label: 'Persist state', icon: '💾', sensitive: false,
    desc: 'Store key-value state between executions, scoped to this function.' },
  { key: 'SCHEDULE', label: 'Scheduled runs', icon: '⏰', sensitive: false,
    desc: 'Run on a schedule its developer configures, subject to plan limits.' },
  { key: 'HTTP_FETCH', label: 'Outbound HTTP', icon: '🌐', sensitive: true,
    desc: 'Make outbound HTTP requests to the public internet from the sandbox. Egress is metered.' },
];

export function capabilityInfo(key: string): CapabilityInfo {
  return (
    CAPABILITY_INFO.find((c) => c.key === key) ?? {
      key,
      label: key.replace(/_/g, ' ').toLowerCase(),
      icon: '▪️',
      desc: 'Capability declared by this app.',
      sensitive: true,
    }
  );
}
