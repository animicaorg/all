import fs from "node:fs";
import path from "node:path";

export const DATA_DIR = path.resolve(path.dirname(new URL(import.meta.url).pathname), "data");
const STORE_PATH = path.join(DATA_DIR, "store.json");

const DEFAULT_STORE = {
  tokens: [],
  pools: [],
  swaps: [],
  liquidityEvents: [],
  reports: [],
  uploads: [],
  activities: [],
  lpPositions: []
};

function ensureStore() {
  fs.mkdirSync(DATA_DIR, { recursive: true });
  if (!fs.existsSync(STORE_PATH)) {
    fs.writeFileSync(STORE_PATH, JSON.stringify(DEFAULT_STORE, null, 2) + "\n", "utf-8");
  }
}

export function readStore() {
  ensureStore();
  const raw = fs.readFileSync(STORE_PATH, "utf-8");
  const parsed = JSON.parse(raw);
  return {
    ...DEFAULT_STORE,
    ...parsed,
    tokens: parsed.tokens || [],
    pools: parsed.pools || [],
    swaps: parsed.swaps || [],
    liquidityEvents: parsed.liquidityEvents || [],
    reports: parsed.reports || [],
    uploads: parsed.uploads || [],
    activities: parsed.activities || [],
    lpPositions: parsed.lpPositions || []
  };
}

export function writeStore(next) {
  ensureStore();
  const tmp = `${STORE_PATH}.tmp`;
  fs.writeFileSync(tmp, JSON.stringify(next, null, 2) + "\n", "utf-8");
  fs.renameSync(tmp, STORE_PATH);
}

export function mutateStore(mutator) {
  const current = readStore();
  const next = mutator(current) || current;
  writeStore(next);
  return next;
}

export function makeId(prefix) {
  const rand = Math.random().toString(36).slice(2, 10);
  return `${prefix}_${Date.now()}_${rand}`;
}
