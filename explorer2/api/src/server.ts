import express from 'express'
import cors from 'cors'
import pino from 'pino'
import pinoHttp from 'pino-http'
import type { ApiError } from '@animica/explorer2-shared'
import { ExplorerService } from './service.js'
import { HttpError } from './errors.js'
import fs from 'node:fs'

interface DiagnosticsInfo {
  mode: string
  rpcUrl: string | null
  chainDbPath: string | null
  chainId: number
  detectedHead: number | null
  timestamp: string
}

export function createServer(service: ExplorerService, corsOrigin: string, logLevel: string, diagnostics?: DiagnosticsInfo) {
  const app = express()
  const logger = pino({ level: logLevel })

  app.use(cors({ origin: corsOrigin }))
  app.use(express.json({ limit: '1mb' }))
  app.use(pinoHttp({ logger: logger as any }))

  app.get('/api/health', async (_req, res) => {
    res.json({ ok: true, timestamp: new Date().toISOString() })
  })

  app.get('/api/meta', async (_req, res) => {
    res.json({
      explorer: {
        name: 'Animica Explorer',
        version: '0.1.0',
        mode: diagnostics?.mode || 'Unknown'
      },
      network: {
        chainId: diagnostics?.chainId || null,
        rpcUrl: diagnostics?.rpcUrl || null
      },
      timestamp: new Date().toISOString()
    })
  })

  app.get('/api/diagnostics', async (_req, res) => {
    try {
      // Get current head from service
      interface HeadData {
        head?: {
          height: number
          hash: string
          time: number
        }
      }
      let currentHead: HeadData['head'] | null = null
      try {
        const headData = await service.getHead() as HeadData
        currentHead = headData?.head ?? null
      } catch (err) {
        // Ignore errors, diagnostics should still work
      }

      const dbPath = diagnostics?.chainDbPath
      let dbExists = false
      let dbSizeBytes: number | null = null
      let dbLastModified: string | null = null

      if (dbPath && typeof dbPath === 'string') {
        try {
          const stats = fs.statSync(dbPath)
          dbExists = true
          dbSizeBytes = stats.size
          dbLastModified = stats.mtime.toISOString()
        } catch {
          // DB doesn't exist or not accessible
        }
      }

      res.json({
        mode: diagnostics?.mode || 'Unknown',
        rpcUrl: diagnostics?.rpcUrl || null,
        chainDbPath: dbPath || null,
        chainId: diagnostics?.chainId || null,
        detectedHead: diagnostics?.detectedHead || null,
        currentHead: currentHead ? {
          height: currentHead.height,
          hash: currentHead.hash,
          time: currentHead.time
        } : null,
        database: {
          exists: dbExists,
          sizeBytes: dbSizeBytes,
          lastModified: dbLastModified
        },
        startupTime: diagnostics?.timestamp || null,
        currentTime: new Date().toISOString()
      })
    } catch (err) {
      res.status(500).json({ 
        error: 'diagnostics_failed', 
        message: err instanceof Error ? err.message : String(err) 
      })
    }
  })

  app.get('/api/head', async (_req, res, next) => {
    try {
      const payload = await service.getHead()
      res.json(payload)
    } catch (err) {
      next(err)
    }
  })

  app.get('/api/blocks', async (req, res, next) => {
    try {
      const limit = Number(req.query.limit || 20)
      const cursor = typeof req.query.cursor === 'string' ? req.query.cursor : undefined
      const payload = await service.getBlocks(limit, cursor)
      res.json(payload)
    } catch (err) {
      next(err)
    }
  })

  app.get('/api/block/:hashOrHeight', async (req, res, next) => {
    try {
      const payload = await service.getBlockDetail(req.params.hashOrHeight)
      res.json(payload)
    } catch (err) {
      next(err)
    }
  })

  app.get('/api/tx/:hash', async (req, res, next) => {
    try {
      const payload = await service.getTxDetail(req.params.hash)
      res.json(payload)
    } catch (err) {
      next(err)
    }
  })

  app.get('/api/address/:bech32', async (req, res, next) => {
    try {
      const limit = Number(req.query.limit || 20)
      const cursor = typeof req.query.cursor === 'string' ? req.query.cursor : undefined
      const payload = await service.getAddressDetail(req.params.bech32, limit, cursor)
      res.json(payload)
    } catch (err) {
      next(err)
    }
  })

  app.get('/api/mempool', async (req, res, next) => {
    try {
      const limit = Number(req.query.limit || 50)
      const cursor = typeof req.query.cursor === 'string' ? req.query.cursor : undefined
      const payload = await service.getMempool(limit, cursor)
      res.json(payload)
    } catch (err) {
      next(err)
    }
  })

  app.get('/api/search', async (req, res, next) => {
    try {
      const query = typeof req.query.q === 'string' ? req.query.q : ''
      const payload = await service.search(query)
      res.json(payload)
    } catch (err) {
      next(err)
    }
  })

  app.get('/api/richlist', async (req, res, next) => {
    try {
      const limit = Number(req.query.limit || 100)
      const offset = Number(req.query.offset || 0)
      const payload = await service.getRichList(limit, offset)
      res.json(payload)
    } catch (err) {
      next(err)
    }
  })

  app.get('/api/richlist/summary', async (_req, res, next) => {
    try {
      const payload = await service.getRichListSummary()
      res.json(payload)
    } catch (err) {
      next(err)
    }
  })

  app.get('/api/debug/rpc', async (_req, res) => {
    if (process.env.NODE_ENV === 'production') {
      res.status(404).json({ error: 'not_found', message: 'Debug endpoints disabled in production' })
      return
    }

    res.json({
      mode: diagnostics?.mode || 'Unknown',
      rpcUrl: diagnostics?.rpcUrl || null,
      timeout: 30000, // From config
      maxRetries: 3,   // From config
      timestamp: new Date().toISOString()
    })
  })

  app.use((err: unknown, _req: express.Request, res: express.Response, _next: express.NextFunction) => {
    if (err instanceof HttpError) {
      const body: ApiError = { error: 'request_failed', message: err.message, detail: err.detail }
      res.status(err.status).json(body)
      return
    }
    const message = err instanceof Error ? err.message : 'Unexpected error'
    res.status(500).json({ error: 'unexpected_error', message })
  })

  return app
}
