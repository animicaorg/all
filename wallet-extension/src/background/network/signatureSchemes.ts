import type { RpcClient } from './rpc';

export type WalletSigScheme = 'dilithium3' | 'sphincs_shake_128s';

export interface NodeSignatureScheme {
  schemeId: number;
  name: string;
  pubkeyLengths?: number[];
  signatureLengths?: number[];
  enabled?: boolean;
  enabledByCode?: boolean;
  enabledByPolicy?: boolean;
  enabledEffective?: boolean;
  reasonIfDisabled?: string | null;
}

const NAME_ALIASES: Record<string, WalletSigScheme> = {
  dilithium3: 'dilithium3',
  sphincs_shake_128s: 'sphincs_shake_128s',
  'sphincs-shake-128s': 'sphincs_shake_128s',
};

function isEnabledEffective(s: NodeSignatureScheme): boolean {
  if (typeof s.enabledEffective === 'boolean') return s.enabledEffective;
  if (typeof s.enabled === 'boolean') return s.enabled;
  return Boolean((s.enabledByCode ?? true) && (s.enabledByPolicy ?? true));
}

export async function fetchSupportedSignatureSchemes(client: RpcClient): Promise<NodeSignatureScheme[]> {
  const result = await client.call<{ schemes?: NodeSignatureScheme[] }>('tx.getSupportedSignatureSchemes');
  const schemes = Array.isArray(result?.schemes) ? result.schemes : [];
  return schemes.filter((s) => s && typeof s.schemeId === 'number' && typeof s.name === 'string');
}

export function chooseBestWalletScheme(
  schemes: NodeSignatureScheme[],
  preferred?: WalletSigScheme,
): WalletSigScheme {
  const enabled = schemes.filter((s) => isEnabledEffective(s));

  const byName = new Map<WalletSigScheme, NodeSignatureScheme>();
  for (const s of enabled) {
    const alias = NAME_ALIASES[s.name.toLowerCase()];
    if (alias && !byName.has(alias)) byName.set(alias, s);
  }

  if (preferred && byName.has(preferred)) {
    return preferred;
  }

  if (byName.has('sphincs_shake_128s')) return 'sphincs_shake_128s';
  if (byName.has('dilithium3')) return 'dilithium3';

  throw new Error('No supported wallet signature scheme enabled on node');
}

export function resolveSchemeIdForWalletAlgo(
  algo: WalletSigScheme,
  schemes: NodeSignatureScheme[],
): number {
  const wanted = schemes.find((s) => isEnabledEffective(s) && NAME_ALIASES[s.name.toLowerCase()] === algo);
  if (!wanted) throw new Error(`Scheme ${algo} not supported by node`);
  return wanted.schemeId;
}

export function resolveWalletSchemeSelection(
  schemes: NodeSignatureScheme[],
  selected?: WalletSigScheme,
): { selected: WalletSigScheme; switchedFrom?: WalletSigScheme; message?: string } {
  const best = chooseBestWalletScheme(schemes, selected);
  if (!selected || selected === best) {
    return { selected: best };
  }
  return {
    selected: best,
    switchedFrom: selected,
    message: `This network currently disallows ${selected}. Switched to ${best}.`,
  };
}
