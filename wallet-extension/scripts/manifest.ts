import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

export type Json = Record<string, any>;

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
export const ROOT = path.resolve(__dirname, "..");

const MANIFEST_BASE = path.join(ROOT, "manifest.base.json");

export async function readManifestBase(): Promise<Json> {
  const raw = await fs.readFile(MANIFEST_BASE, "utf8");
  const cleaned = stripJsonComments(raw);
  return JSON.parse(cleaned) as Json;
}

export function patchForChrome(base: Json): Json {
  const m: Json = structuredClone(base);

  if (!m.minimum_chrome_version) m.minimum_chrome_version = "108";

  if (!m.action) {
    m.action = { default_title: "Animica Wallet", default_popup: "popup.html" };
  } else {
    m.action.default_popup = "popup.html";
  }

  if (!m.background) {
    m.background = { service_worker: "background.js", type: "module" };
  }

  return sortKeys(m);
}

export function patchForFirefox(base: Json): Json {
  const m: Json = structuredClone(base);

  if (!m.background) m.background = {};
  m.background.service_worker = "background.js";
  if (m.background.type) delete m.background.type;

  if (!m.browser_specific_settings) m.browser_specific_settings = {};
  if (!m.browser_specific_settings.gecko) m.browser_specific_settings.gecko = {};

  if (!m.browser_specific_settings.gecko.id) {
    const addonId = process.env.FIREFOX_ADDON_ID || "wallet@animica.dev";
    m.browser_specific_settings.gecko.id = addonId;
  }

  if (!m.minimum_firefox_version) m.minimum_firefox_version = "115.0";
  if (m.minimum_chrome_version) delete m.minimum_chrome_version;

  if (m.content_security_policy?.extension_pages) {
    m.content_security_policy.extension_pages =
      "script-src 'self'; object-src 'self'; base-uri 'self'";
  }

  return sortKeys(m);
}

export function sortKeys(obj: any): any {
  if (Array.isArray(obj)) return obj.map(sortKeys);
  if (obj && typeof obj === "object") {
    return Object.fromEntries(
      Object.keys(obj)
        .sort()
        .map((k) => [k, sortKeys(obj[k])])
    );
  }
  return obj;
}

function stripJsonComments(input: string): string {
  let out = "";
  let inString = false;
  let escaped = false;
  let inLineComment = false;
  let inBlockComment = false;

  for (let i = 0; i < input.length; i++) {
    const ch = input[i];
    const next = input[i + 1];

    if (inLineComment) {
      if (ch === "\n") {
        inLineComment = false;
        out += ch;
      }
      continue;
    }

    if (inBlockComment) {
      if (ch === "*" && next === "/") {
        inBlockComment = false;
        i++; // skip closing slash
      }
      continue;
    }

    if (inString) {
      out += ch;
      if (escaped) {
        escaped = false;
      } else if (ch === "\\") {
        escaped = true;
      } else if (ch === "\"") {
        inString = false;
      }
      continue;
    }

    if (ch === "/" && next === "/") {
      inLineComment = true;
      i++; // skip next
      continue;
    }
    if (ch === "/" && next === "*") {
      inBlockComment = true;
      i++; // skip next
      continue;
    }

    if (ch === "\"") {
      inString = true;
    }

    out += ch;
  }

  return out;
}
