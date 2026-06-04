// Load .env into process.env for tests (Vitest doesn't populate process.env
// from .env the way Next.js does).
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

try {
  const text = readFileSync(resolve(process.cwd(), ".env"), "utf8");
  for (const line of text.split("\n")) {
    const t = line.trim();
    if (!t || t.startsWith("#")) continue;
    const eq = t.indexOf("=");
    if (eq === -1) continue;
    const k = t.slice(0, eq).trim();
    const v = t.slice(eq + 1).trim();
    if (!(k in process.env)) process.env[k] = v;
  }
} catch {
  /* rely on ambient env */
}
