import express from 'express'
import cors from 'cors'
import pino from 'pino'
import pinoHttp from 'pino-http'
import type { ApiError } from '@animica/explorer2-shared'
import { ExplorerService } from './service'
import { HttpError } from './errors'

export function createServer(service: ExplorerService, corsOrigin: string, logLevel: string) {
  const app = express()
  const logger = pino({ level: logLevel })

  app.use(cors({ origin: corsOrigin }))
  app.use(express.json({ limit: '1mb' }))
  app.use(pinoHttp({ logger }))

  app.get('/api/health', async (_req, res) => {
    res.json({ ok: true, timestamp: new Date().toISOString() })
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
