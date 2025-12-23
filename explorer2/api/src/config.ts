import { homedir } from 'node:os'
import path from 'node:path'
import dotenv from 'dotenv'

dotenv.config()

export const config = {
  port: Number(process.env.EXPLORER2_PORT || process.env.PORT || 8081),
  dataRoot: process.env.EXPLORER2_DATA_ROOT || process.env.ANIMICA_DATA_ROOT || path.join(homedir(), '.animica'),
  chainId: Number(process.env.EXPLORER2_CHAIN_ID || process.env.ANIMICA_CHAIN_ID || 1),
  dbPath: process.env.EXPLORER2_DB_PATH || '',
  corsOrigin: process.env.EXPLORER2_CORS_ORIGIN || '*',
  logLevel: process.env.EXPLORER2_LOG_LEVEL || 'info',
  cacheHeadTtlMs: Number(process.env.EXPLORER2_CACHE_HEAD_TTL_MS || 5000),
  cacheBlocksTtlMs: Number(process.env.EXPLORER2_CACHE_BLOCKS_TTL_MS || 8000),
  cacheTxTtlMs: Number(process.env.EXPLORER2_CACHE_TX_TTL_MS || 20000),
  cachePersistPath: process.env.EXPLORER2_CACHE_PERSIST_PATH || ''
}
