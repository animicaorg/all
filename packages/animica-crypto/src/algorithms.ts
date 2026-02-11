/**
 * Algorithm identifiers for Animica PQ cryptography
 */
export enum AlgorithmId {
  /** CRYSTALS-Dilithium3 (ML-DSA-65) - 128-bit security */
  DILITHIUM3 = 0x1001,
  /** SPHINCS+ SHAKE-128s - 128-bit security */
  SPHINCS_SHAKE_128S = 0x1002,
  /** Kyber768 KEM (for P2P only) */
  KYBER768 = 0x2001
}

/**
 * Algorithm metadata
 */
export interface AlgorithmInfo {
  id: number;
  name: string;
  publicKeyLength: number;
  secretKeyLength: number;
  signatureLength: number;
  securityLevel: number;
}

/**
 * Get algorithm information by ID or name
 */
export function getAlgorithmInfo(alg: AlgorithmId | string): AlgorithmInfo {
  const algId = typeof alg === 'string' ? parseAlgorithmName(alg) : alg;
  
  switch (algId) {
    case AlgorithmId.DILITHIUM3:
      return {
        id: 0x1001,
        name: 'dilithium3',
        publicKeyLength: 1952,
        secretKeyLength: 4000,
        signatureLength: 3293,
        securityLevel: 128
      };
    case AlgorithmId.SPHINCS_SHAKE_128S:
      return {
        id: 0x1002,
        name: 'sphincs_shake_128s',
        publicKeyLength: 64,
        secretKeyLength: 64,
        signatureLength: 7856,
        securityLevel: 128
      };
    case AlgorithmId.KYBER768:
      return {
        id: 0x2001,
        name: 'kyber768',
        publicKeyLength: 1184,
        secretKeyLength: 2400,
        signatureLength: 0, // KEM, not signature
        securityLevel: 128
      };
    default:
      throw new Error(`Unknown algorithm ID: 0x${algId.toString(16)}`);
  }
}

/**
 * Parse algorithm name to ID
 */
export function parseAlgorithmName(name: string): AlgorithmId {
  const normalized = name.toLowerCase().replace(/-/g, '_');
  switch (normalized) {
    case 'dilithium3':
    case 'ml_dsa_65':
      return AlgorithmId.DILITHIUM3;
    case 'sphincs_shake_128s':
    case 'sphincs':
      return AlgorithmId.SPHINCS_SHAKE_128S;
    case 'kyber768':
    case 'ml_kem_768':
      return AlgorithmId.KYBER768;
    default:
      throw new Error(`Unknown algorithm name: ${name}`);
  }
}

/**
 * Get algorithm name by ID
 */
export function getAlgorithmName(algId: AlgorithmId): string {
  return getAlgorithmInfo(algId).name;
}
