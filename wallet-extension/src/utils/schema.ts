import { z } from 'zod';

/** Helpers */
const isHex = (s: string) => /^0x[0-9a-fA-F]*$/.test(s);
const isEven = (s: string) => (s.length % 2) === 0;

/** Very lightweight bech32m address check for hrp 'anim' (not a full decode). */
const BECH32M_ANIM_RE = /^anim1[02-9ac-hj-np-z]{38,}$/; // excludes 1,b,i,o; length heuristic

/** Pretty zod error formatter (one-line per issue) */
export function formatZodError(e: z.ZodError): string {
  return e.issues
    .map((i) => {
      const path = i.path.length ? i.path.join('.') : '(root)';
      return `${path}: ${i.message}`;
    })
    .join('; ');
}

/** Wrap parse to throw compact messages */
export function parseOrThrow<T>(schema: z.ZodType<T>, data: unknown, label?: string): T {
  const r = schema.safeParse(data);
  if (!r.success) {
    const msg = formatZodError(r.error);
    throw new Error(label ? `${label} invalid: ${msg}` : msg);
  }
  return r.data;
}

/* ─────────────────────────── Primitive Schemas ──────────────────────────── */

export const NonEmptyString = z.string().min(1);

export const HexString = z
  .string()
  .refine((s) => isHex(s), { message: 'must be 0x-prefixed hex' })
  .refine((s) => isEven(s.slice(2)), { message: 'hex length must be even' });

export const BytesLike = z.union([
  HexString,
  z.instanceof(Uint8Array) as unknown as z.ZodType<Uint8Array>, // compile-time only
]);

export const Bech32mAddress = z
  .string()
  .toLowerCase()
  .refine((s) => BECH32M_ANIM_RE.test(s), { message: 'invalid bech32m anim1… address' });

/** ChainId can be number or numeric string; normalized to number */
export const ChainId = z
  .union([z.number().int().nonnegative(), z.string().regex(/^\d+$/)])
  .transform((v) => (typeof v === 'string' ? Number(v) : v));

/** Safe URL (http/https) */
export const HttpUrl = z
  .string()
  .url()
  .refine((u) => u.startsWith('http://') || u.startsWith('https://'), { message: 'expected http(s) URL' });

/** Integer amounts represented as decimal strings (no fractions) or numbers */
export const UInt = z
  .union([z.number().int().nonnegative(), z.string().regex(/^\d+$/)])
  .transform((v) => (typeof v === 'number' ? String(v) : v));

/* ───────────────────────── Wallet/Network Models ────────────────────────── */

export const NetworkConfig = z.object({
  chainId: ChainId,
  name: NonEmptyString,
  rpcUrl: HttpUrl,
  wsUrl: z.string().url().optional(),
  explorerUrl: z.string().url().optional(),
});
export type NetworkConfig = z.infer<typeof NetworkConfig>;

export const Permission = z.object({
  origin: z.string().url().or(z.string().regex(/^[a-z]+:\/\/[^ ]+$/, 'origin-like string')),
  accounts: z.array(Bech32mAddress).min(1),
  allowedMethods: z.array(z.string()).default([]),
  grantedAt: z.number().int().optional(),
  sessionId: z.string().optional(),
});
export type Permission = z.infer<typeof Permission>;

export const SessionState = z.object({
  selectedAccount: Bech32mAddress,
  selectedNetwork: ChainId,
});
export type SessionState = z.infer<typeof SessionState>;

/* ───────────────────────────── Transaction Types ─────────────────────────── */

export const TxCommon = z.object({
  nonce: z.number().int().nonnegative(),
  from: Bech32mAddress,
  to: Bech32mAddress.optional(), // absent for deploy
  value: UInt.default('0'),
  gasLimit: z.number().int().positive(),
  maxFee: UInt.optional(),
  maxPriorityFee: UInt.optional(),
  data: HexString.default('0x'),
  chainId: ChainId,
});
export type TxCommon = z.infer<typeof TxCommon>;

export const SignBytesDomain = z.object({
  chainId: ChainId,
  domain: z.literal('tx').or(z.literal('permit')).or(z.literal('message')),
});
export type SignBytesDomain = z.infer<typeof SignBytesDomain>;

export const SignRequest = z.object({
  account: Bech32mAddress,
  bytes: BytesLike, // CBOR-encoded sign bytes or message bytes
  domain: SignBytesDomain,
});
export type SignRequest = z.infer<typeof SignRequest>;

/* ───────────────────────── Provider / JSON-RPC Schemas ──────────────────── */

export const JsonRpcId = z.union([z.string(), z.number(), z.null()]);
export const JsonRpcRequest = z.object({
  jsonrpc: z.literal('2.0'),
  id: JsonRpcId.optional(),
  method: z.string().min(1),
  params: z.array(z.unknown()).optional(),
});
export type JsonRpcRequest = z.infer<typeof JsonRpcRequest>;

export const JsonRpcResponse = z.object({
  jsonrpc: z.literal('2.0'),
  id: JsonRpcId,
  result: z.unknown().optional(),
  error: z
    .object({
      code: z.number(),
      message: z.string(),
      data: z.unknown().optional(),
    })
    .optional(),
});
export type JsonRpcResponse = z.infer<typeof JsonRpcResponse>;

export const ProviderConnectRequest = z.object({
  origin: z.string(),
  chainId: ChainId.optional(),
  requestedMethods: z.array(z.string()).optional(),
});
export type ProviderConnectRequest = z.infer<typeof ProviderConnectRequest>;

/* ───────────────────────── Convenience Validators ───────────────────────── */

export const validate = {
  hex: (v: unknown) => parseOrThrow(HexString, v, 'hex'),
  address: (v: unknown) => parseOrThrow(Bech32mAddress, v, 'address'),
  chainId: (v: unknown) => parseOrThrow(ChainId, v, 'chainId'),
  network: (v: unknown) => parseOrThrow(NetworkConfig, v, 'network config'),
  permission: (v: unknown) => parseOrThrow(Permission, v, 'permission'),
  tx: (v: unknown) => parseOrThrow(TxCommon, v, 'transaction'),
  signReq: (v: unknown) => parseOrThrow(SignRequest, v, 'sign request'),
  rpcReq: (v: unknown) => parseOrThrow(JsonRpcRequest, v, 'jsonrpc request'),
  rpcRes: (v: unknown) => parseOrThrow(JsonRpcResponse, v, 'jsonrpc response'),
};

/* ────────────────────────────── Narrowing Helpers ───────────────────────── */

export function isHexString(v: unknown): v is string {
  return typeof v === 'string' && isHex(v) && isEven(v.slice(2));
}

export function isAddress(v: unknown): v is string {
  return typeof v === 'string' && BECH32M_ANIM_RE.test(v);
}

export function isJsonRpcRequest(v: unknown): v is JsonRpcRequest {
  const r = JsonRpcRequest.safeParse(v);
  return r.success;
}
