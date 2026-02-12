/**
 * Safe hex/bytes conversion utilities with strict validation
 * 
 * These utilities ensure that undefined/null values are caught early
 * with clear error messages instead of crashing with "cannot read .slice of undefined"
 */

/**
 * Safely strip 0x prefix from hex string
 * @throws Error if hex is undefined or not a string
 */
export function strip0x(hex: string | undefined | null, fieldName: string = 'hex'): string {
  if (hex === undefined || hex === null) {
    throw new Error(`Expected ${fieldName} to be a string, got ${hex === undefined ? 'undefined' : 'null'}`);
  }
  if (typeof hex !== 'string') {
    throw new Error(`Expected ${fieldName} to be a string, got ${typeof hex}`);
  }
  return hex.startsWith('0x') ? hex.slice(2) : hex;
}

/**
 * Convert hex string to Uint8Array
 * @throws Error if hex is invalid or undefined
 */
export function hexToBytes(hex: string | undefined | null, fieldName: string = 'hex'): Uint8Array {
  const cleaned = strip0x(hex, fieldName);
  
  if (cleaned.length === 0) {
    throw new Error(`${fieldName} is empty`);
  }
  
  if (cleaned.length % 2 !== 0) {
    throw new Error(`${fieldName} has odd length: ${cleaned.length}`);
  }
  
  if (!/^[0-9a-fA-F]*$/.test(cleaned)) {
    throw new Error(`${fieldName} contains invalid hex characters`);
  }
  
  const bytes = new Uint8Array(cleaned.length / 2);
  for (let i = 0; i < bytes.length; i++) {
    bytes[i] = parseInt(cleaned.slice(i * 2, i * 2 + 2), 16);
  }
  return bytes;
}

/**
 * Convert Uint8Array to hex string with 0x prefix
 * @throws Error if bytes is undefined
 */
export function bytesToHex(bytes: Uint8Array | undefined | null, fieldName: string = 'bytes'): string {
  if (bytes === undefined || bytes === null) {
    throw new Error(`Expected ${fieldName} to be Uint8Array, got ${bytes === undefined ? 'undefined' : 'null'}`);
  }
  if (!(bytes instanceof Uint8Array)) {
    throw new Error(`Expected ${fieldName} to be Uint8Array, got ${typeof bytes}`);
  }
  
  return '0x' + Array.from(bytes)
    .map(b => b.toString(16).padStart(2, '0'))
    .join('');
}

/**
 * Convert Uint8Array to hex string without 0x prefix
 */
export function bytesToHexRaw(bytes: Uint8Array | undefined | null, fieldName: string = 'bytes'): string {
  if (bytes === undefined || bytes === null) {
    throw new Error(`Expected ${fieldName} to be Uint8Array, got ${bytes === undefined ? 'undefined' : 'null'}`);
  }
  if (!(bytes instanceof Uint8Array)) {
    throw new Error(`Expected ${fieldName} to be Uint8Array, got ${typeof bytes}`);
  }
  
  return Array.from(bytes)
    .map(b => b.toString(16).padStart(2, '0'))
    .join('');
}

/**
 * Validate that a value is a non-empty string
 * @throws Error if value is not a valid string
 */
export function assertString(value: unknown, fieldName: string): asserts value is string {
  if (typeof value !== 'string') {
    throw new Error(`Expected ${fieldName} to be a string, got ${typeof value}`);
  }
  if (value.length === 0) {
    throw new Error(`${fieldName} is empty`);
  }
}

/**
 * Validate that a value is a valid hex string
 * @throws Error if value is not valid hex
 */
export function assertHex(value: unknown, fieldName: string): asserts value is string {
  assertString(value, fieldName);
  const cleaned = strip0x(value, fieldName);
  if (cleaned.length % 2 !== 0) {
    throw new Error(`${fieldName} has odd length: ${cleaned.length}`);
  }
  if (!/^[0-9a-fA-F]*$/.test(cleaned)) {
    throw new Error(`${fieldName} contains invalid hex characters`);
  }
}

/**
 * Validate that a value is a Uint8Array
 * @throws Error if value is not Uint8Array
 */
export function assertBytes(value: unknown, fieldName: string): asserts value is Uint8Array {
  if (!(value instanceof Uint8Array)) {
    throw new Error(`Expected ${fieldName} to be Uint8Array, got ${typeof value}`);
  }
}

/**
 * Require a specific field exists on an object
 * @throws Error if field is undefined
 */
export function requireField<T, K extends keyof T>(
  obj: T,
  field: K,
  objectName: string = 'object'
): asserts obj is T & Required<Pick<T, K>> {
  if (obj[field] === undefined) {
    throw new Error(`${objectName}.${String(field)} is required but undefined`);
  }
}
