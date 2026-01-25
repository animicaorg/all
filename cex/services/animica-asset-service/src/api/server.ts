/**
 * Express HTTP Server Setup for Animica Asset Service
 */

import express, { type Express } from "express";
import type { Pool } from "pg";
import type { Logger } from "pino";
import type { Config } from "../config.js";
import type { AnimicaRpcClient } from "../rpc/client.js";
import { setupRoutes } from "./routes.js";

/**
 * Create and configure Express server
 */
export function createServer(
  pool: Pool,
  rpcClient: AnimicaRpcClient,
  config: Config,
  logger: Logger
): Express {
  const app = express();

  // Parse JSON bodies
  app.use(express.json());

  // Request logging middleware
  app.use((req, _res, next) => {
    logger.debug(
      {
        method: req.method,
        path: req.path,
        ip: req.ip,
      },
      "HTTP request"
    );
    next();
  });

  // Health check endpoint (no auth required)
  app.get("/healthz", async (_req, res) => {
    try {
      const pgOk = await pool
        .query("SELECT 1")
        .then(() => true)
        .catch(() => false);

      const rpcOk = await rpcClient.health();

      const healthy = pgOk && rpcOk;

      res.status(healthy ? 200 : 503).json({
        status: healthy ? "ok" : "unhealthy",
        service: config.SERVICE_NAME,
        postgres: pgOk,
        rpc: rpcOk,
      });
    } catch (error) {
      logger.error({ error }, "Health check error");
      res.status(503).json({
        status: "unhealthy",
        service: config.SERVICE_NAME,
      });
    }
  });

  // Setup application routes
  setupRoutes(app, pool, rpcClient, config, logger);

  // 404 handler
  app.use((_req, res) => {
    res.status(404).json({
      error: "Not Found",
      message: "Endpoint not found",
    });
  });

  // Error handler
  app.use((err: any, _req: any, res: any, _next: any) => {
    logger.error({ error: err }, "Unhandled error");
    res.status(500).json({
      error: "Internal Server Error",
      message: err.message || "An unexpected error occurred",
    });
  });

  return app;
}
