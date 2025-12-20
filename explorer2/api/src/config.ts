import dotenv from 'dotenv'

dotenv.config()

export const config = {
  port: Number(process.env.EXPLORER2_PORT || process.env.PORT || 8081),
  rpcUrl: process.env.EXPLORER2_RPC_URL || 'http://127.0.0.1:8545/rpc',
  corsOrigin: process.env.EXPLORER2_CORS_ORIGIN || '*',
  logLevel: process.env.EXPLORER2_LOG_LEVEL || 'info',
  cacheHeadTtlMs: Number(process.env.EXPLORER2_CACHE_HEAD_TTL_MS || 5000),
  cacheBlocksTtlMs: Number(process.env.EXPLORER2_CACHE_BLOCKS_TTL_MS || 8000),
  cacheTxTtlMs: Number(process.env.EXPLORER2_CACHE_TX_TTL_MS || 20000)
}
