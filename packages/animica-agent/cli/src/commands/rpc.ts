import { loadConfig, RpcClient, safeParse, safeStringify, toBigInt } from "@animica/agent-core";

import { boolFlag } from "../args.js";
import { fail, info } from "../output.js";

export async function runRpc(positionals: string[], options: Record<string, string | boolean>): Promise<number> {
  const { config } = loadConfig();
  const [verb, method, ...paramTokens] = positionals;
  if (verb !== "call" || !method) {
    fail("usage: animica-agent rpc call <method> [param1] [param2] …");
    return 64;
  }
  const params = paramTokens.map((t) => {
    // Try JSON-parsing each param; fall back to raw string.
    try {
      return safeParse(t);
    } catch {
      if (/^0x[0-9a-fA-F]+$/.test(t) && boolFlag(options, "hex-bigint", false)) {
        try {
          return toBigInt(t);
        } catch {
          return t;
        }
      }
      return t;
    }
  });
  const client = new RpcClient({ url: config.rpcUrl });
  try {
    const result = await client.call({ method, params });
    info(safeStringify({ method, params, result }, { indent: 2 }));
    return 0;
  } catch (err) {
    fail(`RPC ${method} failed: ${(err as Error).message}`);
    return 1;
  }
}
