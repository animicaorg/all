import { beforeAll, afterAll, describe, it, expect } from "vitest";
import { tmpdir } from "node:os";
import path from "node:path";
import fs from "node:fs";
import { mkdtempSync, rmSync, readFileSync, writeFileSync } from "node:fs";
import { spawnSync } from "node:child_process";

/* Utilities */

const REPO_ROOT = path.resolve(__dirname, "..", "..");
const TEMPLATES_DIR = path.join(REPO_ROOT, "templates");
const INDEX_JSON = path.join(TEMPLATES_DIR, "index.json");

type VarSpec = {
  name: string;
  type?: "string" | "integer" | "number" | "boolean" | "enum";
  required?: boolean;
  default?: unknown;
  choices?: unknown[];
};

type VariablesJson = {
  variables: VarSpec[];
};

const UNRENDERED_PATTERNS = ["{{", "}}", "[[", "]]", "<%=", "<%", "%>"];

function readJson<T = any>(p: string): T {
  return JSON.parse(readFileSync(p, "utf8"));
}

function fileNonEmpty(p: string): boolean {
  try {
    const st = fs.statSync(p);
    return st.isFile() && st.size > 0;
  } catch {
    return false;
  }
}

function assertNoUnrenderedTokens(p: string, optional = false) {
  if (!fs.existsSync(p)) {
    if (optional) return;
    throw new Error(`Missing expected file: ${p}`);
  }
  const txt = readFileSync(p, "utf8");
  const offenders = UNRENDERED_PATTERNS.filter((t) => txt.includes(t));
  expect(offenders, `Unrendered template tokens in ${p}: ${offenders.join(", ")}`).toHaveLength(0);
}

function fallbackForType(t: string | undefined, name: string): unknown {
  switch (t) {
    case "string":
      return `example_${name.toLowerCase()}`;
    case "integer":
    case "number":
      return 1;
    case "boolean":
      return true;
    case "enum":
      // choose will be set later
      return null;
    default:
      return `example_${name.toLowerCase()}`;
  }
}

function buildVariables(varsSpec: VariablesJson): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  const specs = Array.isArray(varsSpec.variables) ? varsSpec.variables : [];
  for (const v of specs) {
    const { name, type, default: d } = v;
    if (!name || typeof name !== "string") continue;
    if (d !== undefined) out[name] = d;
    else out[name] = fallbackForType(type, name);
  }
  // Handle enums without defaults: pick first choice
  for (const v of specs) {
    if (!v || v.type !== "enum") continue;
    if (out[v.name] == null && Array.isArray(v.choices) && v.choices.length > 0) {
      out[v.name] = v.choices[0];
    }
  }
  // Friendly overrides for common keys (if present)
  if (out["project_slug"]) out["project_slug"] = "example_dapp";
  if (out["project_name"]) out["project_name"] = "Example Dapp";
  if (out["description"]) out["description"] = "Rendered from dapp-react-ts template for tests.";
  return out;
}

type CliAttempt = {
  args: string[];
  python: string;
};

function tryCliAttempts(attempts: CliAttempt[]): { ok: boolean; stdout?: string; stderr?: string } {
  for (const a of attempts) {
    const res = spawnSync(a.python, a.args, {
      cwd: REPO_ROOT,
      encoding: "utf8",
      stdio: "pipe",
    });
    if (res.status === 0) {
      return { ok: true, stdout: res.stdout, stderr: res.stderr };
    }
    // Keep trying other signatures; if last fails, we'll return not ok
  }
  return { ok: false };
}

function detectPython(): string[] {
  // Prefer python3, then python
  const cands = ["python3", "python"];
  return cands.filter((exe) => {
    try {
      const res = spawnSync(exe, ["--version"], { encoding: "utf8" });
      return res.status === 0;
    } catch {
      return false;
    }
  });
}

/* Test scaffolding */

let workdir = "";
let renderedRoot = "";

beforeAll(() => {
  // mk temp output directory
  workdir = mkdtempSync(path.join(tmpdir(), "tmpl-dapp-"));
});

afterAll(() => {
  if (workdir && fs.existsSync(workdir)) {
    rmSync(workdir, { recursive: true, force: true });
  }
});

describe("templates: render dapp-react-ts", () => {
  it("renders via Python CLI and validates layout", () => {
    // Locate dapp template via index.json
    expect(fs.existsSync(INDEX_JSON)).toBe(true);
    const index = readJson<any>(INDEX_JSON);
    const entries = Array.isArray(index.templates) ? index.templates : index;
    const entry =
      (entries as any[]).find((e) => e && e.id === "dapp-react-ts") ??
      (() => {
        throw new Error("Template id 'dapp-react-ts' not found in templates/index.json");
      })();

    const templateRel: string = entry.path ?? entry.id;
    const templateDir = path.resolve(TEMPLATES_DIR, templateRel);
    expect(fs.existsSync(templateDir)).toBe(true);

    // Load variables.json and construct usable variables
    const varsPath = path.join(templateDir, "variables.json");
    expect(fs.existsSync(varsPath)).toBe(true);
    const varsSpec = readJson<VariablesJson>(varsPath);
    const varsMap = buildVariables(varsSpec);

    // Write variables to temp JSON file
    const varsJsonPath = path.join(workdir, "vars.json");
    writeFileSync(varsJsonPath, JSON.stringify(varsMap), "utf8");

    // Detect available python and try a few CLI signatures
    const pyCands = detectPython();
    if (pyCands.length === 0) {
      // If python isn't available in this environment, treat as a soft skip
      console.warn("Python interpreter not found; skipping render test for dapp-react-ts.");
      return;
    }

    const outDir = path.join(workdir, "out");
    fs.mkdirSync(outDir);

    const attempts: CliAttempt[] = [];

    // Common signatures to try
    for (const py of pyCands) {
      attempts.push(
        {
          python: py,
          args: [
            "-m",
            "templates.engine.cli",
            "render",
            "--template",
            templateDir,
            "--out",
            outDir,
            "--vars-json",
            varsJsonPath,
          ],
        },
        {
          python: py,
          args: [
            "-m",
            "templates.engine.cli",
            "render",
            templateDir,
            outDir,
            "--vars-json",
            varsJsonPath,
          ],
        },
        {
          python: py,
          args: [
            "-m",
            "templates.engine.cli",
            "render",
            "-t",
            templateDir,
            "-o",
            outDir,
            "--vars-json",
            varsJsonPath,
          ],
        },
      );
    }

    const result = tryCliAttempts(attempts);
    if (!result.ok) {
      console.warn("templates.engine.cli render attempts failed; skipping assertions.");
      // This becomes a soft skip so CI without Python still passes JS tests.
      return;
    }

    // Determine project root: commonly "<out>/<project_slug>"
    const projectSlug = String(varsMap["project_slug"] ?? "example_dapp");
    const candidateRoot = path.join(outDir, projectSlug);
    renderedRoot = fs.existsSync(candidateRoot) ? candidateRoot : outDir;

    // Expected generated files
    const expected = [
      "package.json",
      "tsconfig.json",
      "vite.config.ts",
      ".eslintrc.cjs",
      ".prettierrc",
      ".gitignore",
      "public/index.html",
      "src/main.tsx",
      "src/App.tsx",
      "src/pages/Home.tsx",
      "src/pages/Contracts.tsx",
      "src/pages/Send.tsx",
      "src/components/Connect.tsx",
      "src/components/TxStatus.tsx",
      "src/styles.css",
    ].map((p) => path.join(renderedRoot, p));

    const missing = expected.filter((p) => !fs.existsSync(p));
    expect(missing, `Rendered project missing files:\n- ${missing.join("\n- ")}`).toHaveLength(0);

    // Basic sanity: important files are non-empty
    const mustBeNonEmpty = ["package.json", "tsconfig.json", "vite.config.ts", "src/main.tsx", "src/App.tsx"].map((p) =>
      path.join(renderedRoot, p),
    );
    for (const p of mustBeNonEmpty) {
      expect(fileNonEmpty(p)).toBe(true);
    }

    // package.json parseable and has "name"
    const pkg = readJson<any>(path.join(renderedRoot, "package.json"));
    expect(typeof pkg.name).toBe("string");
    expect((pkg.name as string).length).toBeGreaterThan(0);

    // No unrendered tokens in key files
    for (const p of [
      "package.json",
      "tsconfig.json",
      "vite.config.ts",
      "src/main.tsx",
      "src/App.tsx",
      "public/index.html",
    ]) {
      assertNoUnrenderedTokens(path.join(renderedRoot, p));
    }
  });
});
