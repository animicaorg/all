import { encode as cborEncode } from './cbor';
import { bytesToHex } from './bytes';
import { sha3_256 } from './hash';

export type ScriptSource = {
  path: string;
  content: Uint8Array;
};

export type ScriptArtifactContainer = {
  format: string;
  manifest: Record<string, unknown>;
  sources: { path: string; content_b64: string }[];
  compiled_b64: string;
  vm_version: string;
  abi_version: string;
  artifact_hash: string;
};

const SCRIPT_ARTIFACT_FORMAT = 'animica-script-artifact-v1';

function toBase64(bytes: Uint8Array): string {
  if (typeof btoa === 'function') {
    let binary = '';
    for (let i = 0; i < bytes.length; i++) {
      binary += String.fromCharCode(bytes[i]!);
    }
    return btoa(binary);
  }
  // Node or polyfill
  const buf = (globalThis as any).Buffer?.from?.(bytes);
  if (buf) return buf.toString('base64');
  throw new Error('base64 encoder not available');
}

function sortSources(sources: ScriptSource[]): ScriptSource[] {
  return [...sources].sort((a, b) => a.path.localeCompare(b.path));
}

export function buildScriptArtifactContainer(params: {
  manifest: Record<string, unknown>;
  sources: ScriptSource[];
  compiled: Uint8Array;
  vmVersion: string;
  abiVersion: string;
}): ScriptArtifactContainer {
  const sortedSources = sortSources(params.sources);
  const canonicalPayload = {
    format: SCRIPT_ARTIFACT_FORMAT,
    manifest: params.manifest,
    sources: sortedSources.map((src) => ({ path: src.path, bytes: src.content })),
    compiled: params.compiled,
    vmVersion: params.vmVersion,
    abiVersion: params.abiVersion,
  };
  const cbor = cborEncode(canonicalPayload);
  const hashBytes = sha3_256(cbor);
  const artifactHash = '0x' + bytesToHex(hashBytes).slice(2);
  return {
    format: SCRIPT_ARTIFACT_FORMAT,
    manifest: params.manifest,
    sources: sortedSources.map((src) => ({ path: src.path, content_b64: toBase64(src.content) })),
    compiled_b64: toBase64(params.compiled),
    vm_version: params.vmVersion,
    abi_version: params.abiVersion,
    artifact_hash: artifactHash,
  };
}

export function computeCommitment(bytes: Uint8Array): string {
  const hashBytes = sha3_256(bytes);
  return '0x' + bytesToHex(hashBytes).slice(2);
}

export function cborBytes(value: unknown): Uint8Array {
  return cborEncode(value);
}

export function cborHex(value: unknown): string {
  const cbor = cborBytes(value);
  return '0x' + bytesToHex(cbor).slice(2);
}
