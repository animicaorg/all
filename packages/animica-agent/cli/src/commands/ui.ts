import { startBridge } from "@animica/agent-ui";

import { stringFlag } from "../args.js";
import { c, header, info } from "../output.js";

export async function runUI(options: Record<string, string | boolean>): Promise<number> {
  const port = Number.parseInt(stringFlag(options, "port", "4720") as string, 10) || 4720;
  const host = (stringFlag(options, "host", "127.0.0.1") as string) || "127.0.0.1";
  const bridge = startBridge({ port, host });
  header("Animica Coding Agent — local dashboard");
  info(`open ${c.cyan(bridge.url)} in your browser`);
  info(c.dim("Ctrl-C to stop."));
  await new Promise<void>(() => {
    process.on("SIGINT", () => {
      bridge.close();
      process.exit(0);
    });
  });
  return 0;
}
