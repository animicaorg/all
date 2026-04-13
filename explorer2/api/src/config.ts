import { homedir } from 'node:os'
import path from 'node:path'
import dotenv from 'dotenv'

dotenv.config()

// Default RPC URL when not explicitly configured
const DEFAULT_RPC_URL = 'http://127.0.0.1:8545/rpc'

export const config = {
  port: Number(process.env.EXPLORER2_PORT || process.env.PORT || 8081),
  rpcUrl: process.env.EXPLORER2_RPC_URL || process.env.ANIMICA_RPC_URL || DEFAULT_RPC_URL,
  wsUrl: process.env.EXPLORER2_WS_URL || process.env.ANIMICA_WS_URL || '',
  dataRoot: process.env.EXPLORER2_DATA_ROOT || process.env.ANIMICA_DATA_ROOT || path.join(homedir(), '.animica'),
  chainId: Number(process.env.EXPLORER2_CHAIN_ID || process.env.ANIMICA_CHAIN_ID || 1),
  dbPath: process.env.EXPLORER2_DB_PATH || '',
  corsOrigin: process.env.EXPLORER2_CORS_ORIGIN || '*',
  logLevel: process.env.EXPLORER2_LOG_LEVEL || 'info',
  rpcTimeout: Number(process.env.EXPLORER2_RPC_TIMEOUT_MS || 30000),
  rpcMaxRetries: Number(process.env.EXPLORER2_RPC_MAX_RETRIES || 3),
  explorerIndexDbPath:
    process.env.EXPLORER2_INDEX_DB_PATH ||
    process.env.EXPLORER2_STATE_DB_PATH ||
    path.join(
      process.env.EXPLORER2_DATA_ROOT || process.env.ANIMICA_DATA_ROOT || path.join(homedir(), '.animica'),
      'explorer2',
      'explorer2-index.db'
    )
}
