import express from 'express'
import cors from 'cors'
import pino from 'pino'
import pinoHttp from 'pino-http'
import type { ApiError } from '@animica/explorer2-shared'
import { ExplorerService } from './service'
import { HttpError } from './errors'
import fs from 'node:fs'

export function createServer(service: ExplorerService, corsOrigin: string, logLevel: string, diagnostics?: any) {
  const app = express()
  const logger = pino({ level: logLevel })

  app.use(cors({ origin: corsOrigin }))
  app.use(express.json({ limit: '1mb' }))
  app.use(pinoHttp({ logger: logger as any }))

  app.get('/api/health', async (_req, res) => {
    res.json({ ok: true, timestamp: new Date().toISOString() })
  })

  app.get('/api/diagnostics', async (_req, res) => {
    try {
      // Get current head from service
      let currentHead: any = null
      try {
        const headData = await service.getHead()
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
