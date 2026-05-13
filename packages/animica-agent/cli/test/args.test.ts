import { describe, expect, it } from "vitest";

import { parseArgs, boolFlag, stringFlag } from "../src/args.js";

describe("argv parser", () => {
  it("parses subcommand, flags, and positionals", () => {
    const r = parseArgs(["contract", "scaffold", "MyContract", "--force", "--out", "./out"]);
    expect(r.command).toEqual(["contract", "scaffold"]);
    expect(r.positionals).toEqual(["MyContract"]);
    expect(r.options.force).toBe(true);
    expect(r.options.out).toBe("./out");
  });
  it("handles --key=value form", () => {
    const r = parseArgs(["init", "--rpc-url=http://x/rpc"]);
    expect(r.options["rpc-url"]).toBe("http://x/rpc");
  });
  it("handles --no-flag for explicit false", () => {
    const r = parseArgs(["init", "--no-force"]);
    expect(r.options.force).toBe(false);
  });
  it("stops at --", () => {
    const r = parseArgs(["code", "fix bug", "--", "--literal-flag"]);
    expect(r.remainder).toEqual(["--literal-flag"]);
  });
  it("boolFlag and stringFlag coerce sanely", () => {
    expect(boolFlag({ a: "true" }, "a")).toBe(true);
    expect(boolFlag({ a: false }, "a", true)).toBe(false);
    expect(stringFlag({ b: "value" }, "b")).toBe("value");
    expect(stringFlag({}, "b", "def")).toBe("def");
  });
});
