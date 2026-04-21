import { spawn } from "node:child_process";
import path from "node:path";

const ROOT = path.resolve(path.dirname(new URL(import.meta.url).pathname), "..", "..", "..");
const SCRIPT = path.join(ROOT, "scripts", "animica_tokens", "chain_ops.py");

export function runChainOp(command, args = []) {
  const pythonBin = process.env.ANIMICA_PYTHON_BIN || "python";
  const fullArgs = [SCRIPT, command, ...args];

  return new Promise((resolve, reject) => {
    const child = spawn(pythonBin, fullArgs, {
      cwd: ROOT,
      env: {
        ...process.env
      }
    });

    let stdout = "";
    let stderr = "";

    child.stdout.on("data", (chunk) => {
      stdout += chunk.toString("utf-8");
    });

    child.stderr.on("data", (chunk) => {
      stderr += chunk.toString("utf-8");
    });

    child.on("error", (error) => reject(error));

    child.on("close", (code) => {
      let parsed = null;
      try {
        parsed = stdout.trim() ? JSON.parse(stdout) : null;
      } catch {
        parsed = null;
      }

      if (code !== 0 || (parsed && parsed.ok === false)) {
        const message = parsed?.error || stderr || stdout || `chain op failed (${code})`;
        reject(new Error(message));
        return;
      }
      resolve(parsed || { ok: true });
    });
  });
}

export function commonChainArgs() {
  const args = [];
  if (process.env.ANIMICA_RPC_URL) args.push("--rpc", process.env.ANIMICA_RPC_URL);
  if (process.env.ANIMICA_CHAIN_ID) args.push("--chain-id", process.env.ANIMICA_CHAIN_ID);
  if (process.env.ANIMICA_SIGNER_ALG) args.push("--alg", process.env.ANIMICA_SIGNER_ALG);
  if (process.env.ANIMICA_DEPLOY_SEED_HEX) args.push("--seed-hex", process.env.ANIMICA_DEPLOY_SEED_HEX);
  if (process.env.ANIMICA_DEPLOY_MNEMONIC) args.push("--mnemonic", process.env.ANIMICA_DEPLOY_MNEMONIC);
  if (process.env.ANIMICA_MAX_FEE) args.push("--max-fee", process.env.ANIMICA_MAX_FEE);
  if (process.env.ANIMICA_GAS_LIMIT) args.push("--gas-limit", process.env.ANIMICA_GAS_LIMIT);
  if (process.env.ANIMICA_DEX_FACTORY_ADDRESS) args.push("--factory", process.env.ANIMICA_DEX_FACTORY_ADDRESS);
  if (process.env.ANIMICA_DEX_ROUTER_ADDRESS) args.push("--router", process.env.ANIMICA_DEX_ROUTER_ADDRESS);
  return args;
}
