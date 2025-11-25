/**
 * schema.ts — Zod helpers for forms and inputs
 *
 * Lightweight, browser-safe validators that align with Animica conventions:
 *  - Bech32m addresses: anim1... (configurable HRP)
 *  - Hex with optional length guards
 *  - ChainId, bigint, JSON-in-text inputs
 *  - Friendly helpers to surface form errors
 */

import { z, ZodError, ZodIssue } from 'zod';

/* ------------------------------- Primitives ------------------------------- */

/** Positive safe integer (number) with optional max. */
export const zPositiveInt = (max?: number) =>
  z
    .number({ invalid_type_error: 'Expected a number' })
    .int('Must be an integer')
    .positive('Must be > 0')
    .refine((n) => (max == null ? true : n <= max), `Must be ≤ ${max}`);

/** Numeric string of base-10 integer (no decimals), optionally allowing "0x.." hex. */
export const zIntString = (opts?: { allowHex?: boolean }) =>
  z
    .string()
    .trim()
    .refine(
      (s) =>
        (/^[+-]?\d+$/.test(s) && !(s.length > 1 && s.startsWith('+'))) ||
        (!!opts?.allowHex && /^0x[0-9a-fA-F]+$/.test(s)),
      'Expected an integer string',
    );

/** Bigint or decimal-string → bigint. */
export const zBigIntish = z.union([
  z.bigint(),
  z
    .string()
    .trim()
    .regex(/^[+-]?\d+$/, 'Expected integer string')
    .transform((s) => BigInt(s)),
]);

/** ChainId accepts number or string, returns number (safe int). */
export const zChainId = z
  .union([zPositiveInt(Number.MAX_SAFE_INTEGER), zIntString()])
  .transform((v) => (typeof v === 'number' ? v : Number.parseInt(v, 10)))
  .refine((n) => Number.isSafeInteger(n) && n > 0, 'Invalid chainId');

/* --------------------------------- Hexes ---------------------------------- */

export interface HexOpts {
  /** Expected byte length; if provided, validates exact length. */
  lengthBytes?: number;
  /** Permit empty string. */
  emptyOk?: boolean;
  /** Optional description for error messages, e.g. "tx hash". */
  label?: string;
}

/** Validate 0x-prefixed lowercase/uppercase hex; optional exact length in bytes. */
export const zHex = (opts: HexOpts = {}) =>
  z
    .string()
    .trim()
    .transform((s) => (s.startsWith('0x') || s.startsWith('0X') ? s : s ? `0x${s}` : s))
    .refine(
      (s) =>
        !!(opts.emptyOk && s === '') ||
        /^0x[0-9a-fA-F]+$/.test(s),
      () => `${opts.label ?? 'Hex'} must be 0x-prefixed hexadecimal`,
    )
    .refine(
      (s) =>
        opts.lengthBytes == null ||
        s === '' ||
        // "0x" + 2 chars per byte
        s.length === 2 + opts.lengthBytes * 2,
      () =>
        `${opts.label ?? 'Hex'} must be exactly ${opts.lengthBytes} bytes (0x${'..'.repeat(
          opts.lengthBytes ?? 0,
        )})`,
    )
    .transform((s) => (typeof s === 'string' ? s.toLowerCase() : s));

/* -------------------------------- Addresses -------------------------------- */

const BECH32M_CHARSET = /^[qpzry9x8gf2tvdw0s3jn54khce6mua7l]+$/;
const HRP_RE = /^[a-z0-9]+$/;

/**
 * Bech32(m) address with expected HRP (default "anim").
 * This is a lightweight structural check; full checksum validation is left to SDK.
 */
export const zBech32Address = (hrp = 'anim') =>
  z
    .string()
    .trim()
    .min(hrp.length + 8, 'Address seems too short')
    .refine(
      (addr) => addr.toLowerCase().startsWith(hrp.toLowerCase() + '1'),
      () => `Address must start with "${hrp}1"`,
    )
    .refine((addr) => {
      const a = addr.toLowerCase();
      const split = a.indexOf('1');
      if (split < 1) return false;
      const hrpStr = a.slice(0, split);
      const data = a.slice(split + 1);
      return HRP_RE.test(hrpStr) && BECH32M_CHARSET.test(data);
    }, 'Invalid bech32 characters');

/** Either a bech32(m) address with the given HRP or a 32-byte hex (for power users). */
export const zAddress = (hrp = 'anim') =>
  z.union([zBech32Address(hrp), zHex({ lengthBytes: 32, label: 'Address (hex)' })]);

/* ---------------------------------- JSON ---------------------------------- */

/**
 * Parse JSON string and validate against an inner schema.
 * Useful for textareas where user pastes objects/arrays.
 */
export const zJson = <T extends z.ZodTypeAny>(inner: T) =>
  z
    .string()
    .trim()
    .refine((s) => s.length > 0, 'Value is required')
    .transform((s) => {
      try {
        return JSON.parse(s);
      } catch {
        throw new Error('Invalid JSON');
      }
    })
    .pipe(inner);

/* ------------------------------ Base64 bytes ------------------------------ */

export const zBase64 = (opts?: { label?: string; maxBytes?: number }) =>
  z
    .string()
    .trim()
    .refine((s) => /^[A-Za-z0-9+/]+={0,2}$/.test(s), `${opts?.label ?? 'Base64'} is invalid`)
    .refine((s) => {
      if (!opts?.maxBytes) return true;
      try {
        const bytes =
          typeof atob === 'function'
            ? atob(s)
            : Buffer.from(s, 'base64').toString('binary');
        return bytes.length <= opts.maxBytes;
      } catch {
        return false;
      }
    }, `${opts?.label ?? 'Base64'} exceeds max length`);

/* ---------------------------- ABI quick validation ---------------------------- */

export const zAbi = z.object({
  functions: z
    .array(
      z.object({
        name: z.string().min(1),
        inputs: z.array(
          z.object({
            name: z.string().min(1),
            type: z.string().min(1),
          }),
        ),
        outputs: z.array(
          z.object({
            name: z.string().optional(),
            type: z.string().min(1),
          }),
        ),
        stateMutability: z.enum(['view', 'nonpayable', 'payable']).optional(),
      }),
    )
    .default([]),
  events: z
    .array(
      z.object({
        name: z.string().min(1),
        inputs: z.array(
          z.object({
            name: z.string().optional(),
            type: z.string().min(1),
            indexed: z.boolean().optional(),
          }),
        ),
      }),
    )
    .default([]),
  errors: z
    .array(
      z.object({
        name: z.string().min(1),
        inputs: z.array(
          z.object({
            name: z.string().optional(),
            type: z.string().min(1),
          }),
        ),
      }),
    )
    .default([]),
});

/* ------------------------------- Form helpers ------------------------------ */

export type FormErrorBag = Record<string, string[]>;

/** Reduce Zod issues into { path.join('.') : [messages...] } */
export function issuesToErrorBag(issues: ZodIssue[]): FormErrorBag {
  const bag: FormErrorBag = {};
  for (const i of issues) {
    const key = i.path.length ? i.path.join('.') : '_';
    (bag[key] ??= []).push(i.message);
  }
  return bag;
}

/** Return first error per field for simple forms. */
export function firstErrorPerField(error: ZodError): Record<string, string> {
  const bag = issuesToErrorBag(error.issues);
  const single: Record<string, string> = {};
  for (const k of Object.keys(bag)) single[k] = bag[k][0];
  return single;
}

/** Safe parse; if fail, returns { ok:false, errors }. */
export function safeParseWithErrors<T>(
  schema: z.ZodType<T>,
  data: unknown,
): { ok: true; data: T } | { ok: false; errors: Record<string, string> } {
  const res = schema.safeParse(data);
  if (res.success) return { ok: true, data: res.data };
  return { ok: false, errors: firstErrorPerField(res.error) };
}

/** Parse or throw ZodError (useful in non-UI contexts). */
export function parseOrThrow<T>(schema: z.ZodType<T>, data: unknown): T {
  return schema.parse(data);
}

/* --------------------------------- Exports -------------------------------- */

export default {
  zPositiveInt,
  zIntString,
  zBigIntish,
  zChainId,
  zHex,
  zBech32Address,
  zAddress,
  zJson,
  zBase64,
  zAbi,
  issuesToErrorBag,
  firstErrorPerField,
  safeParseWithErrors,
  parseOrThrow,
};
