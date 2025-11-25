import en from "./en.json";
import es from "./es.json";

export type Locale = "en" | "es";
type Primitive = string | number | boolean | null | undefined;
export type Params = Record<string, Primitive>;

type Messages = Record<string, unknown>;
const DICTIONARY: Record<Locale, Messages> = { en, es };

/** Module-local locale (used if no explicit locale is passed to t()). */
let currentLocale: Locale = detectLocale();

/** Try to detect a locale from environment (browser or SSR). */
export function detectLocale(defaultLocale: Locale = "en"): Locale {
  // 1) <html lang="xx"> (SSR-safe if document exists)
  if (typeof document !== "undefined") {
    const lang = (document.documentElement.getAttribute("lang") || "").slice(0, 2).toLowerCase();
    if (isLocale(lang)) return lang;
  }
  // 2) navigator.language / languages (browser only)
  if (typeof navigator !== "undefined") {
    const langs = [navigator.language, ...(navigator.languages || [])]
      .filter(Boolean)
      .map(l => l!.slice(0, 2).toLowerCase());
    for (const l of langs) if (isLocale(l)) return l;
  }
  return defaultLocale;
}

export function getLocale(): Locale {
  return currentLocale;
}
export function setLocale(locale: Locale): void {
  currentLocale = locale;
}

/**
 * Translate a dotted key with optional params.
 * Fallback order: explicit locale -> currentLocale -> English -> key as-is.
 */
export function t(
  key: string,
  params?: Params,
  locale?: Locale
): string {
  const preferred: Locale[] = [
    locale || currentLocale,
    currentLocale,
    "en"
  ].filter(Boolean) as Locale[];

  for (const loc of preferred) {
    const raw = get(DICTIONARY[loc], key);
    if (typeof raw === "string") {
      return interpolate(raw, params);
    }
  }
  // Key not found → return the key (helps catch missing strings in dev)
  return key;
}

/** Resolve "a.b.c" into obj[a][b][c]. */
function get(obj: unknown, path: string): unknown {
  if (!obj) return undefined;
  return path.split(".").reduce<unknown>((acc, part) => {
    if (acc && typeof acc === "object" && part in (acc as Record<string, unknown>)) {
      return (acc as Record<string, unknown>)[part];
    }
    return undefined;
  }, obj);
}

/** Replace {name} with params.name (primitive-stringified). */
function interpolate(template: string, params?: Params): string {
  if (!params) return template;
  return template.replace(/\{(\w+)\}/g, (_, k) => stringify(params[k]));
}

function stringify(v: Primitive): string {
  if (v === null || v === undefined) return "";
  if (typeof v === "boolean") return v ? "true" : "false";
  return String(v);
}

function isLocale(x: string): x is Locale {
  return x === "en" || x === "es";
}

/** Create a scoped translator (useful inside components). */
export function createI18n(scopeLocale?: Locale) {
  return {
    t: (key: string, params?: Params) => t(key, params, scopeLocale),
    locale: scopeLocale ?? currentLocale
  };
}
