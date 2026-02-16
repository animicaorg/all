import { REQUIRED_TRANSACTION_FIELDS, SUPPORTED_MAINNET_RPC_METHODS } from "@/src/lib/animicaGuardrails";

export type ValidationResult = {
  ok: boolean;
  status: "valid" | "rewritten" | "invalid";
  errors: string[];
  rewriteCount: number;
  content: string;
  fixes: string[];
};

const BANNED_PHRASES = [
  /assume this exists/gi,
  /pretend this exists/gi,
  /stand-?in/gi,
  /temporary-fill/gi,
  /to-do/gi
];

const RPC_METHOD_PATTERN = /\b(?:tx|state|chain)\.[A-Za-z][A-Za-z0-9]*\b/g;

function normalizeContract(content: string) {
  const trimmed = content.trim();
  return trimmed.startsWith("contract") ? trimmed : `contract Generated {\n  ${trimmed}\n}`;
}

function stripBannedPhrases(content: string) {
  let next = content;
  let replacements = 0;
  for (const banned of BANNED_PHRASES) {
    next = next.replace(banned, () => {
      replacements += 1;
      return "unsupported in Animica";
    });
  }
  return { content: next, replacements };
}

function removeUnknownRpcMethods(content: string) {
  const matches = content.match(RPC_METHOD_PATTERN) ?? [];
  const unknown = [...new Set(matches.filter((method) => !SUPPORTED_MAINNET_RPC_METHODS.includes(method as any)))];
  let next = content;
  for (const method of unknown) {
    next = next.replaceAll(method, "unsupported RPC method (removed)");
  }
  return { content: next, unknown };
}

function ensureContractRecipe(content: string, fixes: string[]) {
  let next = content;
  const hasContract = /\bcontract\b/.test(next);
  if (!hasContract) return next;

  const missingFields = REQUIRED_TRANSACTION_FIELDS.filter((field) => !new RegExp(`\\b${field}\\b`, "i").test(next));
  if (missingFields.length > 0) {
    fixes.push(`added missing required transaction fields: ${missingFields.join(", ")}`);
    next += "\n\nRequired transaction fields:\n" + missingFields.map((field) => `- ${field}: <set-${field}>`).join("\n");
  }

  if (!/minimal example/i.test(next)) {
    fixes.push("added minimal example section");
    next += "\n\nMinimal Example\n```json\n{\n  \"kind\": \"transfer\",\n  \"data\": {\"to\": \"anim1...\", \"amount\": \"1\"},\n  \"gasLimit\": \"21000\",\n  \"fee\": \"1000\",\n  \"nonce\": \"<state.getNextNonce>\",\n  \"chainId\": \"<chain.getParams.chainId>\"\n}\n```";
  }

  if (!/deployment recipe/i.test(next)) {
    fixes.push("added deployment recipe section");
    next += "\n\nDeployment Recipe\n1. Query nonce via `state.getNextNonce`.\n2. Query chain parameters via `chain.getParams`.\n3. Build raw transaction with `kind`, `data`, `gasLimit`, `fee`, `nonce`, `chainId`.\n4. Decode/check with `tx.decodeRawTransaction`.\n5. Submit with `tx.sendRawTransaction` (or `tx.submitRawTransaction`).\n6. On rejection, call `tx.explainReject` and `tx.debugVerifyRawTransaction` with `rawTx`.";
  }

  return next;
}

export function validateAndRewrite(content: string): ValidationResult {
  const errors: string[] = [];
  const fixes: string[] = [];
  let rewriteCount = 0;
  let next = content;

  if (!content.trim()) {
    return { ok: false, status: "invalid", errors: ["Empty model output"], rewriteCount, content, fixes };
  }

  const banned = stripBannedPhrases(next);
  if (banned.replacements > 0) {
    rewriteCount += banned.replacements;
    fixes.push(`replaced banned phrases (${banned.replacements})`);
    next = banned.content;
  }

  const rpcScan = removeUnknownRpcMethods(next);
  if (rpcScan.unknown.length > 0) {
    rewriteCount += rpcScan.unknown.length;
    fixes.push(`removed unknown RPC methods: ${rpcScan.unknown.join(", ")}`);
    next = rpcScan.content;
  }

  if (!next.includes("contract")) {
    next = normalizeContract(next);
    rewriteCount += 1;
    fixes.push("wrapped output with canonical contract container");
  }

  next = ensureContractRecipe(next, fixes);

  if (/unsupported RPC method \(removed\)/.test(next) && !/unsupported/i.test(next)) {
    errors.push("Unclear unsupported RPC replacement text");
  }

  if (errors.length > 0) {
    return { ok: false, status: "invalid", errors, rewriteCount, content: next, fixes };
  }

  return {
    ok: true,
    status: rewriteCount > 0 || fixes.length > 0 ? "rewritten" : "valid",
    rewriteCount,
    errors: [],
    content: next,
    fixes
  };
}
