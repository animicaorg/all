import { mkdtempSync, readFileSync, rmSync, writeFileSync, mkdirSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import { applyPatch, createPatch, findSensitiveTargets, readLatestJournal, renderPatchPreview, rollbackPatch } from "../src/patches.js";
import { Repo } from "../src/repo.js";

function makeRepo(): { repo: Repo; root: string; journalDir: string } {
  const root = mkdtempSync(join(tmpdir(), "agent-patch-"));
  mkdirSync(join(root, "src"), { recursive: true });
  writeFileSync(join(root, "src/hello.ts"), "export const greet = () => 'hello';\n");
  return { repo: new Repo(root), root, journalDir: join(root, ".journal") };
}

describe("patches", () => {
  it("creates a file via the create op", () => {
    const { repo, root, journalDir } = makeRepo();
    const patch = createPatch("add new file", [{ kind: "create", path: "src/new.ts", contents: "export const x = 1;\n" }]);
    applyPatch({ repo, patch, journalDir });
    expect(readFileSync(join(root, "src/new.ts"), "utf8")).toBe("export const x = 1;\n");
    rmSync(root, { recursive: true });
  });

  it("rejects ambiguous hunks", () => {
    const { repo, root, journalDir } = makeRepo();
    writeFileSync(join(root, "src/hello.ts"), "a\na\nb\n");
    const patch = createPatch("ambiguous", [
      { kind: "edit", path: "src/hello.ts", hunks: [{ oldLines: ["a"], newLines: ["c"] }] },
    ]);
    expect(() => applyPatch({ repo, patch, journalDir })).toThrowError(/ambiguous/i);
    rmSync(root, { recursive: true });
  });

  it("rolls back to byte-identical state", () => {
    const { repo, root, journalDir } = makeRepo();
    const original = readFileSync(join(root, "src/hello.ts"), "utf8");
    const patch = createPatch("replace", [
      { kind: "replace", path: "src/hello.ts", contents: "export const greet = () => 'hi';\n" },
    ]);
    applyPatch({ repo, patch, journalDir });
    const latest = readLatestJournal(journalDir);
    expect(latest).not.toBeNull();
    rollbackPatch(repo, latest!);
    expect(readFileSync(join(root, "src/hello.ts"), "utf8")).toBe(original);
    rmSync(root, { recursive: true });
  });

  it("flags sensitive paths in findSensitiveTargets", () => {
    const patch = createPatch("env", [{ kind: "replace", path: ".env", contents: "X=1" }]);
    expect(findSensitiveTargets(patch)).toContain(".env");
  });

  it("renders preview text with +/- markers", () => {
    const { repo, root } = makeRepo();
    const patch = createPatch("preview", [{ kind: "create", path: "out.txt", contents: "hi" }]);
    const out = renderPatchPreview(patch, repo);
    expect(out).toContain("+++ create out.txt");
    expect(out).toContain("+ hi");
    rmSync(root, { recursive: true });
  });
});
