import { z } from 'zod';

/* -----------------------------------------------------------------------------
 * Hex helpers
 * -------------------------------------------------------------------------- */

const HEX_PREFIX = /^0x/i;

/** Create a zod string schema for a 0x-prefixed fixed-size hex byte-string. */
export const hexBytes = (nBytes: number) =>
  z
    .string()
    .refine((s) => typeof s === 'string' && HEX_PREFIX.test(s), {
      message: 'Must be a 0x-prefixed hex string',
    })
    .refine((s) => s.length === 2 + nBytes * 2, {
      message: `Must be exactly ${nBytes} bytes (0x${'..'.repeat(nBytes)})`,
    })
    .refine((s) => /^[0-9a-fA-F]+$/.test(s.slice(2)), {
      message: 'Contains non-hex characters',
    });

/** Variable-length hex (0x…) with min/max bytes (inclusive). */
export const hexBytesRange = (minBytes: number, maxBytes: number) =>
  z
    .string()
    .refine((s) => typeof s === 'string' && HEX_PREFIX.test(s), {
      message: 'Must be a 0x-prefixed hex string',
    })
    .refine((s) => s.length >= 2 + minBytes * 2 && s.length <= 2 + maxBytes * 2, {
      message: `Must be between ${minBytes}-${maxBytes} bytes`,
    })
    .refine((s) => /^[0-9a-fA-F]+$/.test(s.slice(2)), {
      message: 'Contains non-hex characters',
    });

/* Common fixed-size hashes */
export const Hash256 = hexBytes(32);
export const Hash512 = hexBytes(64);

/* -----------------------------------------------------------------------------
 * Address (anim1… bech32m) — light validator
 * -------------------------------------------------------------------------- */

/**
 * Bech32/bech32m lower-case charset (no 1, b, i, o; with digits 0..9 except 1):
 * 023456789acdefghjklmnpqrstuvwxyz
 *
 * We use a pragmatic check here:
 *  - Must start with "anim1"
 *  - Lowercase only
 *  - Payload length ~ 8..87 (bech32 max)
 * This avoids pulling a full bech32 decoder into the explorer UI layer.
 */
export const Address = z
  .string()
  .transform((s) => s.trim())
  .refine((s) => s === s.toLowerCase(), { message: 'Address must be lowercase' })
  .refine((s) => s.startsWith('anim1'), { message: 'Must start with anim1' })
  .refine(
    (s) => /^anim1[023456789acdefghjklmnpqrstuvwxyz]{8,87}$/.test(s),
    { message: 'Malformed bech32m address (anim1…)' },
  );

/* -----------------------------------------------------------------------------
 * Chain identifiers
 * -------------------------------------------------------------------------- */

export const ChainIdNum = z
  .number({ invalid_type_error: 'chainId must be a number' })
  .int()
  .positive();

export const ChainIdCAIP2 = z
  .string()
  .regex(/^animica:[0-9]+$/, 'Expected CAIP-2 like "animica:1"');

export const ChainId = z.union([ChainIdNum, ChainIdCAIP2]);

/* -----------------------------------------------------------------------------
 * Pagination & common query params
 * -------------------------------------------------------------------------- */

export const Cursor = z.string().min(1).max(256);
export const PageSize = z.number().int().min(1).max(100).default(25);

export const Pagination = z.object({
  cursor: Cursor.optional(),
  limit: PageSize.optional(),
});

export type TPagination = z.infer<typeof Pagination>;

/* -----------------------------------------------------------------------------
 * Blocks / Tx / Event filters
 * -------------------------------------------------------------------------- */

export const BlockNumber = z.number().int().nonnegative();
export const BlockHash = Hash256;

export const TxHash = Hash256;

export const HeightRange = z
  .object({
    from: BlockNumber.optional(),
    to: BlockNumber.optional(),
  })
  .refine(
    (r) => (r.from == null || r.to == null) || (r.from <= r.to),
    { message: '`from` must be <= `to`' },
  );

export type THeightRange = z.infer<typeof HeightRange>;

export const AddressFilter = z.object({
  address: Address,
});

export const EventTopic = hexBytes(32);
export const EventFilter = z.object({
  address: Address.optional(),
  topics: z.array(EventTopic).max(4).optional(),
  fromBlock: BlockNumber.optional(),
  toBlock: BlockNumber.optional(),
}).refine(
  (v) => (v.fromBlock == null || v.toBlock == null) || (v.fromBlock <= v.toBlock),
  { message: '`fromBlock` must be <= `toBlock`' },
);

/* -----------------------------------------------------------------------------
 * Data Availability, AICF, Beacon inputs
 * -------------------------------------------------------------------------- */

export const DACommitment = Hash256; // NMT root commitment (32 bytes)
export const NamespaceId = z.number().int().min(0).max(2 ** 32 - 1);

export const AICFProviderId = z.string().min(1).max(128);
export const AICFJobId = Hash256;
export const AICFJobKind = z.enum(['AI', 'Quantum']);

export const BeaconRoundId = z.number().int().nonnegative();
export const BeaconCommitSalt = hexBytesRange(16, 64); // flexible salt sizes for commits
export const BeaconPayload = hexBytesRange(0, 1024);

/* -----------------------------------------------------------------------------
 * Date/time helpers
 * -------------------------------------------------------------------------- */

export const IsoDate = z
  .string()
  .refine((s) => !Number.isNaN(Date.parse(s)), { message: 'Invalid ISO date/time' });

export const TimeRange = z.object({
  from: IsoDate.optional(),
  to: IsoDate.optional(),
}).refine(
  (r) =>
    (r.from == null || r.to == null) ||
    (new Date(r.from).getTime() <= new Date(r.to).getTime()),
  { message: '`from` must be before or equal to `to`' },
);

/* -----------------------------------------------------------------------------
 * Utilities
 * -------------------------------------------------------------------------- */

/** Safe parse helper that throws with a compact message on failure. */
export function parseOrThrow<T>(schema: z.ZodType<T>, value: unknown, label?: string): T {
  const res = schema.safeParse(value);
  if (!res.success) {
    const msg = res.error.errors.map((e) => `${e.path.join('.') || label || 'value'}: ${e.message}`).join('; ');
    throw new Error(msg);
  }
  return res.data;
}

/* -----------------------------------------------------------------------------
 * Export types for convenience
 * -------------------------------------------------------------------------- */
export type THash256 = z.infer<typeof Hash256>;
export type TAddress = z.infer<typeof Address>;
export type TChainId = z.infer<typeof ChainId>;
export type TBlockNumber = z.infer<typeof BlockNumber>;
export type TTxHash = z.infer<typeof TxHash>;
export type TEventFilter = z.infer<typeof EventFilter>;
export type TDACommitment = z.infer<typeof DACommitment>;
export type TNamespaceId = z.infer<typeof NamespaceId>;
export type TAICFJobId = z.infer<typeof AICFJobId>;
export type TBeaconRoundId = z.infer<typeof BeaconRoundId>;
