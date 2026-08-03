/**
 * @animica/sdk — official JavaScript/TypeScript SDK for the Animica
 * post-quantum L1 blockchain (ANM).
 *
 * Modules:
 *  - rpc:     JsonRpcClient for node JSON-RPC (https://rpc.animica.org/rpc)
 *  - ai:      AnimicaAI, OpenAI-compatible client for https://animica.dev/v1
 *  - address: self-contained bech32m 'anim1…' address codec + validation
 *  - price:   fetchPrice() — ANM/USDT feed from animica.org
 *  - stats:   fetchStats() — public chain stats from animica.org
 */

export {
  DEFAULT_RPC_URL,
  JsonRpcClient,
  RpcError,
  RpcTransportError,
} from "./rpc.js";
export type {
  Hex,
  Head,
  Block,
  BlockRoots,
  ChainParams,
  TotalSupply,
  MempoolStats,
  NetworkHashrate,
  JsonRpcErrorShape,
  JsonRpcClientOptions,
} from "./rpc.js";

export { DEFAULT_AI_BASE_URL, AnimicaAI, parseSseStream } from "./ai.js";
export type {
  AIModel,
  AIModelList,
  ChatRole,
  ChatMessage,
  ChatCompletionRequest,
  ChatCompletion,
  ChatCompletionChoice,
  ChatCompletionChunk,
  ChatCompletionChunkChoice,
  AnimicaAIOptions,
} from "./ai.js";

export {
  ANIMICA_HRP,
  ALG_ID_ML_DSA_65,
  ALG_ID_SPHINCS_PLUS,
  AddressError,
  bech32mEncode,
  bech32mDecode,
  convertBits,
  encodeAddress,
  decodeAddress,
  validateAddress,
  shortAddress,
} from "./address.js";
export type { Bech32Spec, DecodedAddress } from "./address.js";

export { DEFAULT_PRICE_URL, fetchPrice } from "./price.js";
export type { AnmPrice, FetchPriceOptions } from "./price.js";

export { DEFAULT_STATS_URL, fetchStats } from "./stats.js";
export type { ChainStats, FetchStatsOptions } from "./stats.js";

export { HttpError } from "./http.js";
export type { FetchLike, HttpOptions } from "./http.js";
