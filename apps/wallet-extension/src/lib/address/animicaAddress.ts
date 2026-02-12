import { bech32m } from 'bech32';

const DEFAULT_HRP = 'anim';
const DEFAULT_SUPPORTED_VERSIONS = [1, 2] as const;

export interface DecodeAnimAddressOptions {
  expectedHrp?: string;
  supportedVersions?: readonly number[];
}

export interface DecodedAnimAddress {
  hrp: string;
  version: number;
  payload: Uint8Array;
  bytes: Uint8Array;
}

export function decodeAnimAddress(address: string, options: DecodeAnimAddressOptions = {}): DecodedAnimAddress {
  const decoded = bech32m.decode(address);
  const expectedHrp = options.expectedHrp ?? DEFAULT_HRP;
  const supportedVersions = options.supportedVersions ?? DEFAULT_SUPPORTED_VERSIONS;

  if (decoded.prefix !== expectedHrp) {
    throw new Error(`Invalid address prefix: expected ${expectedHrp}, got ${decoded.prefix}`);
  }

  if (decoded.words.length < 1) {
    throw new Error('Invalid address: missing version word');
  }

  const version = decoded.words[0];
  if (version !== 1 && version !== 2) {
    throw new Error(`Unsupported address version: ${version}`);
  }

  if (!supportedVersions.includes(version)) {
    throw new Error(`Unsupported address version ${version} (supported: ${supportedVersions.join(',')})`);
  }

  const payload = new Uint8Array(bech32m.fromWords(decoded.words.slice(1)));
  return {
    hrp: decoded.prefix,
    version,
    payload,
    bytes: payload,
  };
}

export function encodeAnimAddress(
  hrp: string,
  version: number,
  payload: Uint8Array,
): string {
  if (version !== 1 && version !== 2) {
    throw new Error(`Unsupported address version: ${version}`);
  }

  return bech32m.encode(hrp, [version, ...bech32m.toWords(payload)]);
}
