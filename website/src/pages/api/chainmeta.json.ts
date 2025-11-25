import type { APIRoute } from "astro";
import { promises as fs } from "node:fs";
import path from "node:path";
import crypto from "node:crypto";

/**
 * GET /api/chainmeta.json
 *
 * Serves chain metadata aggregated from the repository's `/chains/` folder.
 * Each file in /chains ending with .json is parsed and merged into a single list.
 *
 * Optional query params:
 *  - id=<id>[,id2,...]  Filter by one or more chain ids (matches field `id` or filename stem)
 *
 * Environment overrides:
 *  - CHAINS_DIR: absolute or relative path to the chains directory (defaults to "<repo>/chains")
 */

type ChainRecord = {
  id?: string | number;
  chainId?: number;
  name?: string;
  rpc?: string | string[];
  explorer?: string;
  [k: string]: unknown;
};

const CHAINS_DIR = path.resolve(process.env.CHAINS_DIR || path.join(process.cwd(), "chains"));

async function listJsonFiles(dir: string): Promise<string[]> {
  const entries = await fs.readdir(dir, { withFileTypes: true });
  return entries
    .filter((e) => e.isFile() && /\.json$/i.test(e.name) && !/^\./.test(e.name))
    .map((e) => path.join(dir, e.name));
}

async function readJsonFile(fp: string): Promise<any> {
  const raw = await fs.readFile(fp, "utf8");
  return JSON.parse(raw);
}

function inferIdFromFilename(fp: string): string {
  const base = path.basename(fp);
  return base.replace(/\.json$/i, "");
}

function normalizeRecord(rec: any, fp: string): ChainRecord {
  const id = rec.id ?? inferIdFromFilename(fp);
  // ensure rpc is an array (but keep original too)
  const rpc = Array.isArray(rec.rpc) ? rec.rpc : rec.rpc ? [rec.rpc] : undefined;
  const chainId = typeof rec.chainId === "number" ? rec.chainId : Number(rec.chainId);
  const out: ChainRecord = { ...rec, id, rpc };
  if (Number.isFinite(chainId)) out.chainId = chainId;
  return out;
}

function etagOf(str: string): string {
  return `"W/${crypto.createHash("sha256").update(str).digest("hex").slice(0, 32)}"`;
}

async function latestMtime(files: string[]): Promise<Date | null> {
  let latest: number | null = null;
  for (const f of files) {
    try {
      const st = await fs.stat(f);
      const m = st.mtimeMs || st.mtime.valueOf();
      if (latest == null || m > latest) latest = m;
    } catch {
      // ignore
    }
  }
  return latest ? new Date(latest) : null;
}

export const GET: APIRoute = async ({ request }) => {
  try {
    // Directory must exist
    let files: string[];
    try {
      files = await listJsonFiles(CHAINS_DIR);
    } catch {
      const body = JSON.stringify({ ok: false, error: `chains directory not found: ${CHAINS_DIR}` });
      return new Response(body, {
        status: 404,
        headers: { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" },
      });
    }

    // Load & normalize
    const records: ChainRecord[] = [];
    for (const fp of files) {
      try {
        const rec = await readJsonFile(fp);
        records.push(normalizeRecord(rec, fp));
      } catch (e: any) {
        // Skip bad files but include an error entry for visibility
        records.push({
          id: inferIdFromFilename(fp),
          name: "(invalid json)",
          error: `Failed to parse ${path.basename(fp)}: ${e?.message ?? "unknown error"}`,
        } as any);
      }
    }

    // Filtering by id
    const url = new URL(request.url);
    const idsParam = url.searchParams.get("id");
    let filtered = records;
    if (idsParam) {
      const wanted = new Set(idsParam.split(",").map((s) => s.trim()).filter(Boolean));
      filtered = records.filter((r) => wanted.has(String(r.id)) || (r.chainId != null && wanted.has(String(r.chainId))));
      if (filtered.length === 0) {
        const body = JSON.stringify({ ok: false, error: "No matching chains for id filter", ids: Array.from(wanted) });
        return new Response(body, {
          status: 404,
          headers: { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" },
        });
      }
    }

    // Stable sort by numeric chainId then id
    filtered.sort((a, b) => {
      const ac = Number(a.chainId ?? Number.MAX_SAFE_INTEGER);
      const bc = Number(b.chainId ?? Number.MAX_SAFE_INTEGER);
      if (ac !== bc) return ac - bc;
      return String(a.id).localeCompare(String(b.id));
    });

    const payload = {
      ok: true as const,
      dir: CHAINS_DIR,
      count: filtered.length,
      chains: filtered,
    };

    const json = JSON.stringify(payload);
    const etag = etagOf(json);
    const mtime = await latestMtime(files);

    // Conditional ETag support
    const inm = request.headers.get("if-none-match");
    if (inm && inm === etag) {
      return new Response(null, {
        status: 304,
        headers: {
          etag,
          "cache-control": "public, max-age=60, s-maxage=300, stale-while-revalidate=600",
          ...(mtime ? { "last-modified": mtime.toUTCString() } : {}),
        },
      });
    }

    return new Response(json, {
      status: 200,
      headers: {
        "content-type": "application/json; charset=utf-8",
        "cache-control": "public, max-age=60, s-maxage=300, stale-while-revalidate=600",
        etag,
        ...(mtime ? { "last-modified": mtime.toUTCString() } : {}),
      },
    });
  } catch (err: any) {
    const body = JSON.stringify({ ok: false, error: err?.message ?? "Unknown error" });
    return new Response(body, {
      status: 500,
      headers: { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" },
    });
  }
};
