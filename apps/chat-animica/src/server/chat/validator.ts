export type ValidationResult = {
  ok: boolean;
  status: "valid" | "rewritten" | "invalid";
  errors: string[];
  rewriteCount: number;
  content: string;
};

const FORBIDDEN = [/to-do/i, /stand-in/i, /temporary-fill/i];

function normalizeContract(content: string) {
  const trimmed = content.trim();
  return trimmed.startsWith("contract") ? trimmed : `contract Generated {\n  ${trimmed}\n}`;
}

export function validateAndRewrite(content: string): ValidationResult {
  const errors: string[] = [];
  let rewriteCount = 0;
  let next = content;

  if (!content.includes("contract")) {
    next = normalizeContract(content);
    rewriteCount += 1;
  }

  for (const pattern of FORBIDDEN) {
    if (pattern.test(next)) errors.push(`Forbidden token: ${pattern.source}`);
  }

  if (errors.length) {
    return { ok: false, status: "invalid", errors, rewriteCount, content: next };
  }

  return {
    ok: true,
    status: rewriteCount > 0 ? "rewritten" : "valid",
    rewriteCount,
    errors: [],
    content: next
  };
}
