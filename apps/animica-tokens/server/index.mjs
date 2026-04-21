import fs from "node:fs";
import path from "node:path";
import express from "express";
import cors from "cors";
import multer from "multer";
import { z } from "zod";

import { commonChainArgs, runChainOp } from "./chain.mjs";
import { localIpfsPath, persistMediaFile, persistMetadataJson } from "./ipfs.mjs";
import { DATA_DIR, makeId, mutateStore, readStore } from "./store.mjs";
import {
  findPoolByTokens,
  isNativeToken,
  normalizeTokenAddress,
  parsePositiveInt,
  sanitizeDescription,
  sanitizeText,
  sanitizeUrl,
  toIsoNow
} from "./util.mjs";

const HERE = path.dirname(new URL(import.meta.url).pathname);

function loadEnvFile(filePath) {
  if (!fs.existsSync(filePath)) return;
  const lines = fs.readFileSync(filePath, "utf-8").split(/\r?\n/);
  for (const line of lines) {
    if (!line || line.startsWith("#") || !line.includes("=")) continue;
    const [key, ...rest] = line.split("=");
    const value = rest.join("=").trim();
    if (!process.env[key.trim()]) {
      process.env[key.trim()] = value;
    }
  }
}

loadEnvFile(path.join(HERE, ".env"));
loadEnvFile(path.resolve(HERE, "..", ".env.local"));

const app = express();
const PORT = Number(process.env.ANIMICA_TOKENS_SERVER_PORT || 8787);
const MAX_UPLOAD_BYTES = Number(process.env.ANIMICA_UPLOAD_MAX_BYTES || 8 * 1024 * 1024);
const MIME_ALLOWLIST = new Set(["image/png", "image/jpeg", "image/jpg", "image/gif"]);

app.use(cors());
app.use(express.json({ limit: "2mb" }));

const upload = multer({
  storage: multer.memoryStorage(),
  limits: {
    fileSize: MAX_UPLOAD_BYTES
  }
});

function notFound(res, message) {
  res.status(404).json({ error: message });
}

function calcAmountOut(amountIn, reserveIn, reserveOut, feeBps) {
  if (amountIn <= 0 || reserveIn <= 0 || reserveOut <= 0) return 0;
  const amountInWithFee = amountIn * (10000 - feeBps);
  const numerator = amountInWithFee * reserveOut;
  const denominator = reserveIn * 10000 + amountInWithFee;
  if (denominator <= 0) return 0;
  return Math.floor(numerator / denominator);
}

function calcAmountIn(amountOut, reserveIn, reserveOut, feeBps) {
  if (amountOut <= 0 || reserveIn <= 0 || reserveOut <= 0 || amountOut >= reserveOut) return 0;
  const numerator = reserveIn * amountOut * 10000;
  const denominator = (reserveOut - amountOut) * (10000 - feeBps);
  if (denominator <= 0) return 0;
  return Math.floor(numerator / denominator) + 1;
}

function parseTxHash(result) {
  return (
    result?.receipt?.txHash ||
    result?.receipt?.transactionHash ||
    result?.txHash ||
    undefined
  );
}

function ensureAdmin(req, res) {
  const key = req.header("x-admin-key");
  const expected = process.env.ANIMICA_TOKENS_ADMIN_KEY;
  if (!expected) return true;
  if (key !== expected) {
    res.status(401).json({ error: "unauthorized" });
    return false;
  }
  return true;
}

app.get("/api/health", (_req, res) => {
  res.json({ ok: true, service: "animica-tokens-api", time: new Date().toISOString() });
});

app.get("/api/stats", (_req, res) => {
  const store = readStore();
  const now = Date.now();
  const cutoff = now - 24 * 60 * 60 * 1000;
  const swapCount24h = store.swaps.filter((swap) => new Date(swap.createdAt).getTime() >= cutoff).length;
  const liquidityNotional = store.pools.reduce((sum, pool) => {
    return sum + Number(pool.reserveA || 0) + Number(pool.reserveB || 0);
  }, 0);
  res.json({
    tokenCount: store.tokens.filter((token) => !token.hidden).length,
    poolCount: store.pools.length,
    swapCount24h,
    liquidityNotional: String(liquidityNotional)
  });
});

app.post("/api/upload/media", upload.single("file"), async (req, res) => {
  try {
    if (!req.file) {
      res.status(400).json({ error: "file is required" });
      return;
    }

    const mime = String(req.file.mimetype || "").toLowerCase();
    if (!MIME_ALLOWLIST.has(mime)) {
      res.status(400).json({ error: "unsupported file type" });
      return;
    }

    const safeName = sanitizeText(req.file.originalname || "upload", 120).replace(/\s+/g, "_");
    const pinned = await persistMediaFile(req.file.buffer, safeName, mime);

    mutateStore((store) => {
      store.uploads.push({
        id: makeId("upload"),
        kind: "media",
        cid: pinned.cid,
        uri: pinned.uri,
        gatewayUrl: pinned.gatewayUrl,
        filename: safeName,
        mime,
        size: req.file.size,
        createdAt: toIsoNow()
      });
      return store;
    });

    res.json({ cid: pinned.cid, uri: pinned.uri, gatewayUrl: pinned.gatewayUrl });
  } catch (error) {
    res.status(500).json({ error: String(error.message || error) });
  }
});

app.post("/api/upload/metadata", async (req, res) => {
  try {
    const body = req.body || {};
    const metadata = {
      name: sanitizeText(body.name, 64),
      symbol: sanitizeText(body.symbol, 16),
      description: sanitizeDescription(body.description),
      image: sanitizeText(body.image, 512),
      animation_url: sanitizeText(body.animation_url, 512),
      website: sanitizeUrl(body.website),
      twitter: sanitizeUrl(body.twitter),
      telegram: sanitizeUrl(body.telegram),
      discord: sanitizeUrl(body.discord),
      github: sanitizeUrl(body.github),
      creator: sanitizeText(body.creator, 128),
      created_at: toIsoNow(),
      chain_id: Number(process.env.ANIMICA_CHAIN_ID || 1337),
      decimals: Number(body.decimals || 18),
      total_supply: String(body.total_supply || "0")
    };

    const pinned = await persistMetadataJson(metadata);

    mutateStore((store) => {
      store.uploads.push({
        id: makeId("upload"),
        kind: "metadata",
        cid: pinned.cid,
        uri: pinned.uri,
        gatewayUrl: pinned.gatewayUrl,
        size: pinned.raw.length,
        createdAt: toIsoNow()
      });
      return store;
    });

    res.json({ cid: pinned.cid, uri: pinned.uri, gatewayUrl: pinned.gatewayUrl });
  } catch (error) {
    res.status(500).json({ error: String(error.message || error) });
  }
});

app.get("/api/tokens", (req, res) => {
  const q = sanitizeText(req.query.q || "", 128).toLowerCase();
  const store = readStore();
  const list = store.tokens
    .filter((token) => !token.hidden)
    .filter((token) => {
      if (!q) return true;
      const hay = `${token.name} ${token.symbol} ${token.address}`.toLowerCase();
      return hay.includes(q);
    })
    .sort((a, b) => (a.createdAt < b.createdAt ? 1 : -1));
  res.json(list);
});

app.get("/api/tokens/:tokenId", (req, res) => {
  const tokenId = String(req.params.tokenId);
  const store = readStore();
  const token = store.tokens.find((item) => item.id === tokenId || item.address === tokenId);
  if (!token || token.hidden) return notFound(res, "token not found");

  const tokenPools = store.pools.filter(
    (pool) => normalizeTokenAddress(pool.tokenAAddress) === normalizeTokenAddress(token.address) || normalizeTokenAddress(pool.tokenBAddress) === normalizeTokenAddress(token.address)
  );
  const poolIds = new Set(tokenPools.map((pool) => pool.id));
  const swaps = store.swaps.filter((swap) => poolIds.has(swap.pairId)).slice(-80).reverse();
  const liquidity = store.liquidityEvents.filter((item) => poolIds.has(item.pairId)).slice(-80).reverse();

  res.json({ token, swaps, liquidity });
});

app.post("/api/tokens/launch", async (req, res) => {
  try {
    const schema = z.object({
      name: z.string().min(1).max(64),
      symbol: z.string().min(1).max(16),
      decimals: z.coerce.number().int().min(0).max(255),
      initialSupply: z.string(),
      maxSupply: z.string(),
      mintable: z.boolean().default(false),
      metadataUri: z.string().min(1),
      creatorAddress: z.string().min(1),
      freezeAuthority: z.string().optional(),
      description: z.string().optional(),
      imageUri: z.string().optional(),
      website: z.string().optional(),
      twitter: z.string().optional(),
      telegram: z.string().optional(),
      discord: z.string().optional(),
      github: z.string().optional()
    });
    const payload = schema.parse(req.body);

    const args = [
      ...commonChainArgs(),
      "--name", payload.name,
      "--symbol", payload.symbol,
      "--decimals", String(payload.decimals),
      "--initial-supply", payload.initialSupply,
      "--max-supply", payload.maxSupply,
      "--metadata-uri", payload.metadataUri,
      "--owner-address", payload.creatorAddress
    ];
    if (payload.freezeAuthority) {
      args.push("--freeze-authority", payload.freezeAuthority);
    }
    if (payload.mintable) {
      args.push("--mintable");
    }

    const chainResult = await runChainOp("launch-token", args);
    const tokenAddress = String(chainResult.token);

    const tokenRecord = {
      id: makeId("token"),
      address: tokenAddress,
      name: sanitizeText(payload.name, 64),
      symbol: sanitizeText(payload.symbol, 16),
      decimals: payload.decimals,
      description: sanitizeDescription(payload.description || ""),
      metadataUri: sanitizeText(payload.metadataUri, 512),
      imageUri: sanitizeText(payload.imageUri || "", 512),
      website: sanitizeUrl(payload.website),
      twitter: sanitizeUrl(payload.twitter),
      telegram: sanitizeUrl(payload.telegram),
      discord: sanitizeUrl(payload.discord),
      github: sanitizeUrl(payload.github),
      creator: payload.creatorAddress,
      createdAt: toIsoNow(),
      hidden: false,
      totalSupply: payload.initialSupply,
      maxSupply: payload.maxSupply,
      mintable: payload.mintable,
      deployTxHash: parseTxHash(chainResult.deploy_receipt),
      initTxHash: parseTxHash(chainResult.init_receipt)
    };

    mutateStore((store) => {
      store.tokens.unshift(tokenRecord);
      store.activities.unshift({
        id: makeId("activity"),
        type: "token_launch",
        actor: payload.creatorAddress,
        tokenId: tokenRecord.id,
        createdAt: toIsoNow()
      });
      return store;
    });

    res.json({ token: tokenRecord });
  } catch (error) {
    res.status(400).json({ error: String(error.message || error) });
  }
});

app.get("/api/pools", (_req, res) => {
  const store = readStore();
  res.json(store.pools.sort((a, b) => (a.createdAt < b.createdAt ? 1 : -1)));
});

app.get("/api/pools/:pairId", (req, res) => {
  const pairId = String(req.params.pairId);
  const store = readStore();
  const pool = store.pools.find((item) => item.id === pairId || item.pairAddress === pairId);
  if (!pool) return notFound(res, "pool not found");

  const swaps = store.swaps.filter((swap) => swap.pairId === pool.id).slice(-80).reverse();
  const liquidity = store.liquidityEvents.filter((item) => item.pairId === pool.id).slice(-80).reverse();
  res.json({ pool, swaps, liquidity });
});

app.post("/api/pools/create", async (req, res) => {
  try {
    const schema = z.object({
      tokenA: z.string().min(1),
      tokenB: z.string().min(1),
      feeBps: z.coerce.number().int().min(1).max(300).default(30),
      metadataUri: z.string().optional(),
      creatorAddress: z.string().min(1),
      launchFeeAnm: z.coerce.number().int().min(0).default(0)
    });
    const payload = schema.parse(req.body);

    const args = [
      ...commonChainArgs(),
      "--token-a", payload.tokenA,
      "--token-b", payload.tokenB,
      "--fee-bps", String(payload.feeBps),
      "--metadata-uri", payload.metadataUri || "",
      "--launch-fee-anm", String(payload.launchFeeAnm)
    ];

    const chainResult = await runChainOp("create-pair", args);

    const store = readStore();
    const tokenARecord = store.tokens.find((t) => normalizeTokenAddress(t.address) === normalizeTokenAddress(payload.tokenA));
    const tokenBRecord = store.tokens.find((t) => normalizeTokenAddress(t.address) === normalizeTokenAddress(payload.tokenB));

    const poolRecord = {
      id: makeId("pool"),
      pairAddress: String(chainResult.pair),
      tokenA: isNativeToken(payload.tokenA) ? "ANM" : (tokenARecord?.symbol || "TOKEN_A"),
      tokenB: isNativeToken(payload.tokenB) ? "ANM" : (tokenBRecord?.symbol || "TOKEN_B"),
      tokenAAddress: normalizeTokenAddress(payload.tokenA),
      tokenBAddress: normalizeTokenAddress(payload.tokenB),
      feeBps: payload.feeBps,
      reserveA: "0",
      reserveB: "0",
      lpSupply: "0",
      metadataUri: sanitizeText(payload.metadataUri || "", 512),
      createdAt: toIsoNow(),
      creator: payload.creatorAddress,
      deployTxHash: parseTxHash(chainResult.pair_deploy_receipt),
      createTxHash: parseTxHash(chainResult.create_receipt)
    };

    mutateStore((next) => {
      next.pools.unshift(poolRecord);
      next.activities.unshift({
        id: makeId("activity"),
        type: "pool_create",
        actor: payload.creatorAddress,
        pairId: poolRecord.id,
        createdAt: toIsoNow()
      });
      return next;
    });

    res.json({ pool: poolRecord });
  } catch (error) {
    res.status(400).json({ error: String(error.message || error) });
  }
});

app.post("/api/dex/quote", (req, res) => {
  try {
    const schema = z.object({
      tokenIn: z.string().min(1),
      tokenOut: z.string().min(1),
      amountIn: z.string().optional(),
      amountOut: z.string().optional(),
      mode: z.enum(["exactIn", "exactOut"]).default("exactIn")
    });
    const payload = schema.parse(req.body);

    const store = readStore();
    const pool = findPoolByTokens(store.pools, payload.tokenIn, payload.tokenOut);
    if (!pool) {
      res.json({ ok: false, error: "pair not found" });
      return;
    }

    const aNorm = normalizeTokenAddress(payload.tokenIn);
    const inOnA = normalizeTokenAddress(pool.tokenAAddress) === aNorm;
    const reserveIn = Number(inOnA ? pool.reserveA : pool.reserveB);
    const reserveOut = Number(inOnA ? pool.reserveB : pool.reserveA);
    const feeBps = Number(pool.feeBps || 30);

    if (payload.mode === "exactOut") {
      const amountOut = parsePositiveInt(payload.amountOut || "0", "amountOut");
      const amountIn = calcAmountIn(amountOut, reserveIn, reserveOut, feeBps);
      res.json({ ok: amountIn > 0, amountIn: String(amountIn), amountOut: String(amountOut), feeBps });
      return;
    }

    const amountIn = parsePositiveInt(payload.amountIn || "0", "amountIn");
    const amountOut = calcAmountOut(amountIn, reserveIn, reserveOut, feeBps);
    const impact = reserveIn > 0 ? Math.floor((amountIn * 10000) / reserveIn) : 0;

    res.json({
      ok: amountOut > 0,
      amountIn: String(amountIn),
      amountOut: String(amountOut),
      priceImpactBps: impact,
      feeBps
    });
  } catch (error) {
    res.status(400).json({ ok: false, error: String(error.message || error) });
  }
});

app.post("/api/dex/swap", async (req, res) => {
  try {
    const schema = z.object({
      tokenIn: z.string().min(1),
      tokenOut: z.string().min(1),
      amountIn: z.string(),
      minAmountOut: z.string().default("0"),
      traderAddress: z.string().min(1),
      toAddress: z.string().min(1),
      deadline: z.coerce.number().int().default(0)
    });
    const payload = schema.parse(req.body);

    const store = readStore();
    const pool = findPoolByTokens(store.pools, payload.tokenIn, payload.tokenOut);
    if (!pool) {
      res.status(400).json({ error: "pair not found" });
      return;
    }

    const amountIn = parsePositiveInt(payload.amountIn, "amountIn");
    const minAmountOut = parsePositiveInt(payload.minAmountOut, "minAmountOut");
    const tokenInNorm = normalizeTokenAddress(payload.tokenIn);

    const inOnA = normalizeTokenAddress(pool.tokenAAddress) === tokenInNorm;
    const reserveIn = Number(inOnA ? pool.reserveA : pool.reserveB);
    const reserveOut = Number(inOnA ? pool.reserveB : pool.reserveA);
    const amountOut = calcAmountOut(amountIn, reserveIn, reserveOut, Number(pool.feeBps));

    if (amountOut < minAmountOut) {
      res.status(400).json({ error: "slippage check failed" });
      return;
    }

    const chainArgs = [
      ...commonChainArgs(),
      "--token-in", payload.tokenIn,
      "--token-out", payload.tokenOut,
      "--amount-in", String(amountIn),
      "--min-amount-out", String(minAmountOut),
      "--to-address", payload.toAddress,
      "--deadline", String(payload.deadline)
    ];
    const chainResult = await runChainOp("swap-exact-in", chainArgs);

    const swapRecord = {
      id: makeId("swap"),
      pairId: pool.id,
      pairAddress: pool.pairAddress,
      tokenIn: inOnA ? pool.tokenA : pool.tokenB,
      tokenOut: inOnA ? pool.tokenB : pool.tokenA,
      amountIn: String(amountIn),
      amountOut: String(amountOut),
      trader: payload.traderAddress,
      txHash: parseTxHash(chainResult),
      createdAt: toIsoNow()
    };

    mutateStore((next) => {
      const target = next.pools.find((item) => item.id === pool.id);
      if (target) {
        if (inOnA) {
          target.reserveA = String(Number(target.reserveA) + amountIn);
          target.reserveB = String(Math.max(0, Number(target.reserveB) - amountOut));
        } else {
          target.reserveB = String(Number(target.reserveB) + amountIn);
          target.reserveA = String(Math.max(0, Number(target.reserveA) - amountOut));
        }
      }
      next.swaps.unshift(swapRecord);
      next.activities.unshift({
        id: makeId("activity"),
        type: "swap",
        actor: payload.traderAddress,
        pairId: pool.id,
        swapId: swapRecord.id,
        createdAt: toIsoNow()
      });
      return next;
    });

    res.json({ swap: swapRecord });
  } catch (error) {
    res.status(400).json({ error: String(error.message || error) });
  }
});

app.post("/api/dex/liquidity/add", async (req, res) => {
  try {
    const schema = z.object({
      pairId: z.string().min(1),
      amountA: z.string(),
      amountB: z.string(),
      providerAddress: z.string().min(1),
      deadline: z.coerce.number().int().default(0)
    });
    const payload = schema.parse(req.body);

    const store = readStore();
    const pool = store.pools.find((p) => p.id === payload.pairId || p.pairAddress === payload.pairId);
    if (!pool) return notFound(res, "pool not found");

    const amountA = parsePositiveInt(payload.amountA, "amountA");
    const amountB = parsePositiveInt(payload.amountB, "amountB");

    const reserveA = Number(pool.reserveA || 0);
    const reserveB = Number(pool.reserveB || 0);
    const lpSupply = Number(pool.lpSupply || 0);

    const minted = lpSupply === 0
      ? Math.floor(Math.sqrt(amountA * amountB))
      : Math.floor(Math.min((amountA * lpSupply) / Math.max(1, reserveA), (amountB * lpSupply) / Math.max(1, reserveB)));

    const chainArgs = [
      ...commonChainArgs(),
      "--token-a", pool.tokenAAddress,
      "--token-b", pool.tokenBAddress,
      "--amount-a", String(amountA),
      "--amount-b", String(amountB),
      "--min-lp", "0",
      "--deadline", String(payload.deadline)
    ];
    const chainResult = await runChainOp("add-liquidity", chainArgs);

    const liquidityRecord = {
      id: makeId("liq"),
      pairId: pool.id,
      pairAddress: pool.pairAddress,
      provider: payload.providerAddress,
      kind: "add",
      amountA: String(amountA),
      amountB: String(amountB),
      lpAmount: String(Math.max(0, minted)),
      txHash: parseTxHash(chainResult),
      createdAt: toIsoNow()
    };

    mutateStore((next) => {
      const target = next.pools.find((p) => p.id === pool.id);
      if (target) {
        target.reserveA = String(Number(target.reserveA) + amountA);
        target.reserveB = String(Number(target.reserveB) + amountB);
        target.lpSupply = String(Number(target.lpSupply) + Math.max(0, minted));
      }
      const existingPosition = next.lpPositions.find((p) => p.pairId === pool.id && p.provider === payload.providerAddress);
      if (existingPosition) {
        existingPosition.lpAmount = String(Number(existingPosition.lpAmount) + Math.max(0, minted));
        existingPosition.updatedAt = toIsoNow();
      } else {
        next.lpPositions.push({
          id: makeId("lppos"),
          pairId: pool.id,
          pairAddress: pool.pairAddress,
          provider: payload.providerAddress,
          lpAmount: String(Math.max(0, minted)),
          createdAt: toIsoNow(),
          updatedAt: toIsoNow()
        });
      }
      next.liquidityEvents.unshift(liquidityRecord);
      return next;
    });

    res.json({ liquidity: liquidityRecord });
  } catch (error) {
    res.status(400).json({ error: String(error.message || error) });
  }
});

app.post("/api/dex/liquidity/remove", async (req, res) => {
  try {
    const schema = z.object({
      pairId: z.string().min(1),
      lpAmount: z.string(),
      providerAddress: z.string().min(1),
      minAmountA: z.coerce.number().int().default(0),
      minAmountB: z.coerce.number().int().default(0),
      deadline: z.coerce.number().int().default(0)
    });
    const payload = schema.parse(req.body);

    const store = readStore();
    const pool = store.pools.find((p) => p.id === payload.pairId || p.pairAddress === payload.pairId);
    if (!pool) return notFound(res, "pool not found");

    const lpAmount = parsePositiveInt(payload.lpAmount, "lpAmount");
    const reserveA = Number(pool.reserveA || 0);
    const reserveB = Number(pool.reserveB || 0);
    const lpSupply = Number(pool.lpSupply || 0);

    if (lpSupply <= 0 || lpAmount <= 0 || lpAmount > lpSupply) {
      res.status(400).json({ error: "invalid LP amount" });
      return;
    }

    const amountA = Math.floor((lpAmount * reserveA) / lpSupply);
    const amountB = Math.floor((lpAmount * reserveB) / lpSupply);

    if (amountA < payload.minAmountA || amountB < payload.minAmountB) {
      res.status(400).json({ error: "slippage check failed" });
      return;
    }

    const chainArgs = [
      ...commonChainArgs(),
      "--token-a", pool.tokenAAddress,
      "--token-b", pool.tokenBAddress,
      "--lp-amount", String(lpAmount),
      "--min-amount-a", String(payload.minAmountA),
      "--min-amount-b", String(payload.minAmountB),
      "--deadline", String(payload.deadline)
    ];
    const chainResult = await runChainOp("remove-liquidity", chainArgs);

    const liquidityRecord = {
      id: makeId("liq"),
      pairId: pool.id,
      pairAddress: pool.pairAddress,
      provider: payload.providerAddress,
      kind: "remove",
      amountA: String(amountA),
      amountB: String(amountB),
      lpAmount: String(lpAmount),
      txHash: parseTxHash(chainResult),
      createdAt: toIsoNow()
    };

    mutateStore((next) => {
      const target = next.pools.find((p) => p.id === pool.id);
      if (target) {
        target.reserveA = String(Math.max(0, Number(target.reserveA) - amountA));
        target.reserveB = String(Math.max(0, Number(target.reserveB) - amountB));
        target.lpSupply = String(Math.max(0, Number(target.lpSupply) - lpAmount));
      }

      const position = next.lpPositions.find((p) => p.pairId === pool.id && p.provider === payload.providerAddress);
      if (position) {
        position.lpAmount = String(Math.max(0, Number(position.lpAmount) - lpAmount));
        position.updatedAt = toIsoNow();
      }

      next.liquidityEvents.unshift(liquidityRecord);
      return next;
    });

    res.json({ liquidity: liquidityRecord });
  } catch (error) {
    res.status(400).json({ error: String(error.message || error) });
  }
});

app.get("/api/portfolio/:address", (req, res) => {
  const address = String(req.params.address || "").trim();
  const store = readStore();

  const createdTokens = store.tokens.filter((token) => token.creator === address && !token.hidden);
  const positions = store.lpPositions
    .filter((position) => position.provider === address)
    .map((position) => {
      const pool = store.pools.find((p) => p.id === position.pairId);
      const totalLp = Number(pool?.lpSupply || 0);
      const lp = Number(position.lpAmount || 0);
      const shareBps = totalLp > 0 ? Math.floor((lp * 10000) / totalLp) : 0;
      return {
        pairId: position.pairId,
        pairAddress: position.pairAddress,
        lpAmount: position.lpAmount,
        tokenA: pool?.tokenA || "?",
        tokenB: pool?.tokenB || "?",
        shareBps
      };
    });

  const recentSwaps = store.swaps.filter((swap) => swap.trader === address).slice(0, 20);
  const recentLiquidity = store.liquidityEvents.filter((item) => item.provider === address).slice(0, 20);
  const recentActivity = [...recentSwaps, ...recentLiquidity]
    .sort((a, b) => (a.createdAt < b.createdAt ? 1 : -1))
    .slice(0, 40);

  res.json({
    address,
    createdTokens,
    lpPositions: positions,
    recentActivity
  });
});

app.post("/api/reports", (req, res) => {
  try {
    const schema = z.object({
      tokenId: z.string().min(1),
      reason: z.string().min(1).max(160),
      reporter: z.string().min(1)
    });
    const payload = schema.parse(req.body);

    const report = {
      id: makeId("report"),
      tokenId: payload.tokenId,
      reason: sanitizeText(payload.reason, 160),
      reporter: sanitizeText(payload.reporter, 128),
      createdAt: toIsoNow(),
      resolved: false
    };

    mutateStore((store) => {
      store.reports.unshift(report);
      return store;
    });

    res.json({ ok: true });
  } catch (error) {
    res.status(400).json({ error: String(error.message || error) });
  }
});

app.get("/api/admin/reports", (req, res) => {
  if (!ensureAdmin(req, res)) return;
  const store = readStore();
  res.json(store.reports);
});

app.post("/api/admin/tokens/:tokenId/visibility", (req, res) => {
  if (!ensureAdmin(req, res)) return;

  const schema = z.object({ hidden: z.boolean() });
  const payload = schema.parse(req.body || {});
  const tokenId = String(req.params.tokenId);

  const updated = mutateStore((store) => {
    const token = store.tokens.find((item) => item.id === tokenId || item.address === tokenId);
    if (!token) return store;
    token.hidden = payload.hidden;
    return store;
  });

  const token = updated.tokens.find((item) => item.id === tokenId || item.address === tokenId);
  if (!token) return notFound(res, "token not found");

  res.json({ ok: true, token });
});

app.get("/ipfs/:cid", (req, res) => {
  const cid = sanitizeText(req.params.cid, 256);
  const p = localIpfsPath(cid);
  if (!fs.existsSync(p)) return notFound(res, "cid not found");
  res.sendFile(p);
});

app.use((err, _req, res, _next) => {
  res.status(500).json({ error: err?.message || String(err) });
});

app.listen(PORT, () => {
  fs.mkdirSync(DATA_DIR, { recursive: true });
  console.log(`[animica-tokens-api] listening on http://127.0.0.1:${PORT}`);
});
